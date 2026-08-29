from __future__ import annotations

import json
import logging
from enum import Enum

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .settings import settings

log = logging.getLogger("ai-pr-reviewer.review")


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Finding(BaseModel):
    path: str
    line: int
    severity: Severity
    title: str
    detail: str
    suggestion: str | None = None


class ReviewOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    overall: str = ""


class ResolvedStatus(BaseModel):
    path: str
    title: str
    status: str  # "resolved" | "open" | "unknown"
    note: str = ""


class ResolvedOutput(BaseModel):
    items: list[ResolvedStatus] = Field(default_factory=list)


# Built once at import, on the main thread. A fresh genai.Client per call closes
# the shared httpx transport on GC ("Cannot send a request, client closed"), and
# building it inside anyio's threadpool leaves the transport orphaned. Worker
# threads reuse this instance (httpx sync client is safe across threads).
_CLIENT = genai.Client(
    vertexai=True,
    project=settings.gcp_project,
    location=settings.vertex_location,
)


def _client() -> genai.Client:
    return _CLIENT


def _patch_blob(files: list[dict]) -> str:
    out = []
    for f in files:
        patch = f.get("patch") or "(no textual diff — binary or too large)"
        if len(patch) > settings.max_patch_chars:
            patch = patch[: settings.max_patch_chars] + "\n... [truncated] ..."
        out.append(f"### FILE: {f['filename']}  (status: {f['status']})\n{patch}")
    return "\n\n".join(out)


def run_review(prompt: str, model: str, thinking_budget: int, files: list[dict]) -> ReviewOutput:
    contents = (
        "Review the following pull request diff and return findings as JSON.\n\n"
        + _patch_blob(files)
    )
    resp = _client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=prompt,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=ReviewOutput,
            thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
        ),
    )
    try:
        return resp.parsed or ReviewOutput.model_validate_json(resp.text)
    except Exception:  # noqa: BLE001 - never let a bad model response 500 the webhook
        log.exception("could not parse review output; raw=%s", getattr(resp, "text", None))
        return ReviewOutput()


def check_resolved(prior: list[dict], model: str, files: list[dict]) -> list[ResolvedStatus]:
    if not prior:
        return []
    contents = (
        "Prior review findings (JSON):\n"
        + json.dumps(prior, ensure_ascii=False)
        + "\n\nCurrent PR diff:\n\n"
        + _patch_blob(files)
        + "\n\nFor each prior finding, decide whether it is now `resolved` in the "
        "current diff, still `open`, or `unknown`. Keep the same path/title."
    )
    resp = _client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=ResolvedOutput,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    try:
        parsed = resp.parsed or ResolvedOutput.model_validate_json(resp.text)
        return parsed.items
    except Exception:  # noqa: BLE001
        log.exception("could not parse resolved-check output")
        return []
