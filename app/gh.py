from __future__ import annotations

import hashlib
import hmac
import re

from github import Auth, Github, GithubIntegration

from .settings import settings

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_BOT_LOGIN: str | None = None


def verify_signature(body: bytes, sig_header: str | None) -> bool:
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    digest = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", sig_header)


def _integration() -> GithubIntegration:
    return GithubIntegration(
        auth=Auth.AppAuth(int(settings.github_app_id), settings.private_key)
    )


def client_for_installation(installation_id: int) -> Github:
    return _integration().get_github_for_installation(installation_id)


def app_bot_login() -> str:
    """Login GitHub uses for this App's comments, e.g. 'genai-code-reviewer-flexe[bot]'."""
    global _BOT_LOGIN
    if _BOT_LOGIN is None:
        _BOT_LOGIN = f"{_integration().get_app().slug}[bot]"
    return _BOT_LOGIN


def commentable_lines(patch: str | None) -> set[int]:
    """New-file line numbers a PR review comment can attach to (added + context)."""
    lines: set[int] = set()
    if not patch:
        return lines
    new_no = 0
    for raw in patch.splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            new_no = int(m.group(1))
            continue
        if raw.startswith("+"):
            lines.add(new_no)
            new_no += 1
        elif raw.startswith("-"):
            continue
        elif raw.startswith(" "):
            lines.add(new_no)
            new_no += 1
    return lines


if __name__ == "__main__":
    sample = "@@ -1,3 +1,4 @@\n a\n-b\n+b2\n+b3\n c\n"
    # new file lines: 1=" a" ctx, 2="+b2", 3="+b3", 4=" c" ctx
    assert commentable_lines(sample) == {1, 2, 3, 4}, commentable_lines(sample)
    assert commentable_lines(None) == set()
    assert verify_signature(b"x", None) is False
    assert verify_signature(b"x", "sha256=deadbeef") is False
    print("gh self-check ok")
