"""Config parsing + startup guard checks. Run: python -m tests.test_config

No framework: plain asserts. Sets dummy required env before importing settings
so it works with no real credentials.
"""
import os

os.environ.setdefault("GITHUB_APP_ID", "1")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "dummy")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "x" * 20)
os.environ.setdefault("GENAI_API_KEY", "dummy")

from app.settings import Settings  # noqa: E402


def s(**over) -> Settings:
    base = dict(
        github_app_id="1",
        github_app_private_key="dummy",
        github_webhook_secret="x" * 20,
        genai_api_key="dummy",
    )
    base.update(over)
    return Settings(**base)


def test_allowed_org_set():
    assert s(allowed_orgs="").allowed_org_set == set()
    assert s(allowed_orgs="  A , b ,,C ").allowed_org_set == {"a", "b", "c"}


def test_exclude_list():
    assert s(exclude_globs="a, b ,, c").exclude_list == ["a", "b", "c"]


def test_check_rejects_weak_secret():
    try:
        s(github_webhook_secret="short").check()
    except RuntimeError:
        pass
    else:
        raise AssertionError("weak webhook secret should raise")


def test_check_requires_llm_backend():
    try:
        s(genai_api_key="", gcp_project="").check()
    except RuntimeError:
        pass
    else:
        raise AssertionError("missing LLM backend should raise")


def test_check_warns_on_empty_allowed_orgs():
    warnings = s(allowed_orgs="").check()
    assert any("ALLOWED_ORGS" in w for w in warnings)
    assert s(allowed_orgs="acme").check() == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all config checks passed")
