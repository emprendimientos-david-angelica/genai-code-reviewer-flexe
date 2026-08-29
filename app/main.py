from __future__ import annotations

import logging
import threading
from fnmatch import fnmatch

import sentry_sdk
from fastapi import FastAPI, Header, Request, Response

from . import gh, review
from .settings import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("genai-code-reviewer-flexe")

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )

app = FastAPI(title="genai-code-reviewer-flexe")

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

_SEV_ES = {"critical": "CRÍTICA", "high": "ALTA", "medium": "MEDIA", "low": "BAJA"}
_SEV_ORDER = ["critical", "high", "medium", "low"]


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "genai-code-reviewer-flexe"}


def _spawn(installation_id: int, repo_full: str, pr_number: int) -> None:
    # A dedicated OS thread, not anyio's threadpool: the google-genai sync client
    # misbehaves ("httpx client closed") when driven from that pool.
    threading.Thread(
        target=_run, args=(installation_id, repo_full, pr_number), daemon=True
    ).start()


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    body = await request.body()
    if not gh.verify_signature(body, x_hub_signature_256):
        return Response("bad signature", status_code=401)

    payload = await request.json()
    action = payload.get("action")

    if x_github_event == "pull_request" and action in REVIEW_ACTIONS:
        pr = payload["pull_request"]
        if not pr.get("draft"):
            _spawn(
                payload["installation"]["id"],
                payload["repository"]["full_name"],
                pr["number"],
            )
    elif x_github_event == "issue_comment" and action == "created":
        issue = payload.get("issue", {})
        comment = payload.get("comment", {}).get("body") or ""
        if issue.get("pull_request") and settings.review_command in comment:
            _spawn(
                payload["installation"]["id"],
                payload["repository"]["full_name"],
                issue["number"],
            )

    return Response(status_code=202)


def _collect_files(pr) -> list[dict]:
    files = []
    for f in pr.get_files()[: settings.max_files]:
        if any(fnmatch(f.filename, p) for p in settings.exclude_list):
            continue
        files.append(
            {"filename": f.filename, "status": f.status, "patch": getattr(f, "patch", None)}
        )
    return files


def _run(installation_id: int, repo_full: str, pr_number: int) -> None:
    try:
        client = gh.client_for_installation(installation_id)
        pr = client.get_repo(repo_full).get_pull(pr_number)

        files = _collect_files(pr)
        if not files:
            log.info("no reviewable files for %s#%s", repo_full, pr_number)
            return

        result = review.run_review(
            settings.prompt, settings.model, settings.thinking_budget, files
        )

        me = gh.app_bot_login()
        prior = [
            {"path": c.path, "title": c.body[:160]}
            for c in pr.get_review_comments()
            if c.user and c.user.login == me
        ]
        resolved = review.check_resolved(prior, settings.model, files)

        valid = {f["filename"]: gh.commentable_lines(f["patch"]) for f in files}
        inline: list[dict] = []
        spilled: list[str] = []
        for fnd in result.findings[: settings.max_findings]:
            block = f"**[{_SEV_ES[fnd.severity.value]}] {fnd.title}**\n\n{fnd.detail}"
            if fnd.suggestion:
                block += f"\n\n```suggestion\n{fnd.suggestion}\n```"
            if fnd.line in valid.get(fnd.path, set()):
                inline.append(
                    {"path": fnd.path, "line": fnd.line, "side": "RIGHT", "body": block}
                )
            else:
                spilled.append(f"- `{fnd.path}:{fnd.line}` — {block}")

        pr.create_review(
            body=_summary_md(result, resolved, spilled),
            event="COMMENT",
            comments=inline,
        )
        log.info(
            "reviewed %s#%s: %d inline, %d spilled, %d rechecked",
            repo_full, pr_number, len(inline), len(spilled), len(resolved),
        )
    except Exception:  # noqa: BLE001 - background task, log and move on
        log.exception("review failed for %s#%s", repo_full, pr_number)
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("repo", repo_full)
            scope.set_context("pr", {"number": pr_number})
            sentry_sdk.capture_exception()


def _summary_md(result: review.ReviewOutput, resolved, spilled: list[str]) -> str:
    by_sev: dict[str, int] = {}
    for f in result.findings:
        by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1

    out = ["## 🤖 genai-code-reviewer-flexe — Gemini 2.5 Flash", ""]
    if by_sev:
        out.append(
            " · ".join(
                f"**{_SEV_ES[s]}**: {by_sev[s]}" for s in _SEV_ORDER if by_sev.get(s)
            )
        )
    else:
        out.append("Sin hallazgos que bloqueen el merge.")
    if result.overall:
        out += ["", result.overall]

    if resolved:
        icon = {"resolved": "✅", "open": "❌", "unknown": "❔"}
        out += ["", "### Re-chequeo de hallazgos previos"]
        out += [
            f"- {icon.get(r.status, '❔')} `{r.path}` — {r.title}"
            + (f" · {r.note}" if r.note else "")
            for r in resolved
        ]

    if spilled:
        out += ["", "### Hallazgos fuera de las líneas modificadas", *spilled]

    out += [
        "",
        "---",
        f"_Volver a correr la revisión: comentá `{settings.review_command}` en el PR._",
    ]
    return "\n".join(out)
