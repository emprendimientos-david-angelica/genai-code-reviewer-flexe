from __future__ import annotations

import logging
import threading
import time
from fnmatch import fnmatch

import sentry_sdk
from fastapi import FastAPI, Header, Request, Response

from .settings import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-pr-reviewer")

# Validate before importing `review`, which builds the model client at import.
for _w in settings.check():
    log.warning(_w)

from . import gh, review  # noqa: E402  (after settings.check on purpose)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )

app = FastAPI(title="ai-pr-reviewer")

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

_SEV_ES = {"critical": "CRÍTICA", "high": "ALTA", "medium": "MEDIA", "low": "BAJA"}
_SEV_ORDER = ["critical", "high", "medium", "low"]


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "ai-pr-reviewer"}


def _spawn(
    installation_id: int,
    repo_full: str,
    pr_number: int,
    *,
    forced: bool = False,
    head_sha: str | None = None,
) -> None:
    # A dedicated OS thread, not anyio's threadpool: the google-genai sync client
    # misbehaves ("httpx client closed") when driven from that pool.
    threading.Thread(
        target=_run,
        args=(installation_id, repo_full, pr_number, forced, head_sha),
        daemon=True,
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

    allowed = settings.allowed_org_set
    if allowed:
        owner = (payload.get("repository", {}).get("owner", {}).get("login") or "").lower()
        if owner not in allowed:
            log.info("ignoring event from unlisted org %r", owner)
            return Response(status_code=204)

    if x_github_event == "pull_request" and action in REVIEW_ACTIONS:
        pr = payload["pull_request"]
        if not pr.get("draft"):
            _spawn(
                payload["installation"]["id"],
                payload["repository"]["full_name"],
                pr["number"],
                head_sha=pr.get("head", {}).get("sha"),
            )
    elif x_github_event == "issue_comment" and action == "created":
        issue = payload.get("issue", {})
        comment = payload.get("comment", {}).get("body") or ""
        if issue.get("pull_request") and settings.review_command in comment:
            _spawn(
                payload["installation"]["id"],
                payload["repository"]["full_name"],
                issue["number"],
                forced=True,
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


def _run(
    installation_id: int,
    repo_full: str,
    pr_number: int,
    forced: bool = False,
    head_sha: str | None = None,
) -> None:
    try:
        # Debounce a burst of pushes: wait, then bail if a newer push landed —
        # the event for that push will do the review instead.
        if not forced and head_sha and settings.debounce_seconds > 0:
            time.sleep(settings.debounce_seconds)

        client = gh.client_for_installation(installation_id)
        pr = client.get_repo(repo_full).get_pull(pr_number)
        me = gh.app_bot_login()

        if not forced and head_sha and pr.head.sha != head_sha:
            log.info(
                "skipping %s#%s: head moved %s -> %s",
                repo_full, pr_number, head_sha[:7], pr.head.sha[:7],
            )
            return
        fetched_sha = pr.head.sha

        # Iteration cap. Automatic re-reviews stop after N; a `/genai-review`
        # comment (forced) always runs. The last review's footer already tells
        # the user how to force one.
        if not forced:
            n_prior = sum(
                1 for r in pr.get_reviews() if r.user and r.user.login == me
            )
            if n_prior >= settings.max_auto_reviews:
                log.info(
                    "skipping %s#%s: auto-review cap %d reached",
                    repo_full, pr_number, settings.max_auto_reviews,
                )
                return

        files = _collect_files(pr)
        if not files:
            log.info("no reviewable files for %s#%s", repo_full, pr_number)
            return

        result = review.run_review(
            settings.prompt, settings.model, settings.thinking_budget, files
        )

        prior = [
            {"path": c.path, "title": c.body[:160]}
            for c in pr.get_review_comments()
            if c.user and c.user.login == me
        ][-settings.resolved_recheck_limit :]
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

        # Gemini can take a while (two sequential calls). If new commits landed
        # on the PR meanwhile, our line numbers no longer match GitHub's current
        # diff and create_review would 422 on the *whole* batch — bail instead of
        # losing every finding. The next push (or /genai-review) reviews fresh.
        pr.update()
        if pr.head.sha != fetched_sha:
            log.info(
                "skipping %s#%s: head moved %s -> %s mid-review, diff is stale",
                repo_full, pr_number, fetched_sha[:7], pr.head.sha[:7],
            )
            return

        pr.create_review(
            body=_summary_md(result, resolved, spilled),
            event="COMMENT",
            comments=inline,
        )
        log.info(
            "reviewed %s#%s: %d inline, %d spilled, %d rechecked",
            repo_full, pr_number, len(inline), len(spilled), len(resolved),
        )
    except Exception as exc:  # noqa: BLE001 - background task, log and move on
        # 429 from the model is expected backpressure (shared quota), not a bug —
        # log it, don't page.
        if getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc):
            log.warning("review rate-limited for %s#%s: %s", repo_full, pr_number, exc)
            return
        log.exception("review failed for %s#%s", repo_full, pr_number)
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("repo", repo_full)
            scope.set_context("pr", {"number": pr_number})
            sentry_sdk.capture_exception()


def _summary_md(result: review.ReviewOutput, resolved, spilled: list[str]) -> str:
    by_sev: dict[str, int] = {}
    for f in result.findings:
        by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1

    out = [f"## 🤖 Revisión automática — {settings.model}", ""]
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
