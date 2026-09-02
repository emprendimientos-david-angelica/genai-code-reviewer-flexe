# Contributing

Thanks for helping out. This is a small codebase — five files in `app/` — keep
changes small and focused.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in at least the required vars
uvicorn app.main:app --reload --port 8080
```

Use a tunnel (`cloudflared tunnel --url http://localhost:8080`) as the webhook
URL while iterating against a real test App.

## Before opening a PR

```bash
python -m app.gh             # diff-hunk parser + signature guard self-check
python -m tests.test_config  # settings / allowlist / exclude parsing
```

If you touch review logic, describe how you tested it against a real PR.

## Scope

- Bug fixes and small quality improvements: just open a PR.
- New LLM providers: isolate the change to `app/review.py`; keep the existing
  Vertex and API-key paths working.
- Larger features (queue-based processing, per-repo prompts, GitHub Enterprise
  support): open an issue first so we can agree on the shape.

## Style

Match the surrounding code. Type hints, small functions, comments that explain
*why* not *what*. No new dependencies unless a few lines can't do the job.
