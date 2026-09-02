# ai-pr-reviewer

A self-hosted GitHub App that reviews pull requests with Google's **Gemini**
models and posts the findings back on the PR.

- **Inline comments** on the changed lines, one per finding, each with a severity
  (`critical` / `high` / `medium` / `low`) and an optional `suggestion` block.
- **One summary review** at the end of the PR: counts by severity, an overall
  note, and any findings that landed outside the diff.
- **Re-review on every push** to the PR (`synchronize`) and on a `/genai-review`
  comment. It re-runs the review **and** re-checks the previous findings,
  marking each ✅ resolved / ❌ open / ❔ unknown.
- **One prompt for the whole install** — [`prompt.md`](prompt.md) in this repo.
  Edit it, redeploy, done. No per-repo config.
- **No database.** Prior findings are read back from the App's own review
  comments on the PR.
- **Comment-only.** The bot never approves, requests changes, or merges. A
  malicious diff can at worst produce a noisy comment.

> The reference deployment runs on **Cloud Run + Vertex AI**. It also runs with a
> plain **Gemini API key** and no GCP at all — see [LLM backend](#llm-backend).

---

## How it works

```
GitHub PR event ──HTTPS──▶  /webhook  (FastAPI)
                              │  verify HMAC signature (X-Hub-Signature-256)
                              │  drop events from orgs not in ALLOWED_ORGS
                              │  mint an installation token (GitHub App)
                              │  GET PR files + patches
                              ├─▶ Gemini: diff → findings JSON
                              ├─▶ Gemini: prior findings → resolved?
                              └─▶ POST PR review: inline comments + summary
```

The review runs in a background thread; the webhook returns `202` immediately.

---

## Quick start

You need: a GitHub org (or user) you can create an App on, and either a Gemini
API key or a GCP project with Vertex AI enabled.

### 1. Create the GitHub App

GitHub can't create Apps via API, so do this in the UI:
**Org → Settings → Developer settings → GitHub Apps → New GitHub App**.

| Field | Value |
|---|---|
| Webhook → Active | ✅ |
| Webhook URL | `https://PLACEHOLDER/webhook` (fix after first deploy) |
| Webhook secret | a long random string — this is `GITHUB_WEBHOOK_SECRET` |
| Repository permissions → **Pull requests** | Read and write |
| Repository permissions → **Metadata** | Read-only (automatic) |
| Subscribe to events | ✅ **Pull request**, ✅ **Issue comment** |
| Where can this be installed? | *Only on this account* (see [multi-org](#using-it-on-more-than-one-org)) |

Then on the App page: note the **App ID**, and **Generate a private key**
(downloads a `.pem`). Don't install it yet.

### 2. Configure

Copy [`.env.example`](.env.example) to `.env` and fill it in. Minimum:

```
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END ...\n"
GITHUB_WEBHOOK_SECRET=...            # >= 16 chars, matches the App
GENAI_API_KEY=...                   # or GCP_PROJECT=... for Vertex
ALLOWED_ORGS=my-org                 # strongly recommended
```

### 3. Run it

**Locally:**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
# expose it: cloudflared tunnel --url http://localhost:8080
```

**Docker:**

```bash
docker build -t ai-pr-reviewer .
docker run -p 8080:8080 --env-file .env ai-pr-reviewer
```

**Cloud Run:** the included [deploy workflow](.github/workflows/deploy.yml)
builds the image, pushes it to Artifact Registry and deploys. It reads
`GCP_PROJECT_ID`, `GCP_SA_KEY`, `GH_APP_ID` from Actions **secrets** and lets you
override region / service name / secret names via Actions **variables** (see the
comment at the top of the file). Secrets `GITHUB_APP_PRIVATE_KEY` and
`GITHUB_WEBHOOK_SECRET` are pulled from GCP Secret Manager at deploy time.

### 4. Wire the webhook and install

1. Put the deployed URL (`https://.../webhook`) into the App's **Webhook URL**.
2. App → **Install App** → pick the repos you want reviewed.
3. Open a PR. The review lands within a minute.

The **Install App** page is the per-repo on/off switch.

---

## LLM backend

Pick one, in `.env` / the environment:

| Mode | Set | Notes |
|---|---|---|
| **Gemini API key** | `GENAI_API_KEY` | Simplest. Runs anywhere. Get a key from Google AI Studio. |
| **Vertex AI** | `GCP_PROJECT` (leave `GENAI_API_KEY` empty) | No key in env — uses Application Default Credentials / the Cloud Run runtime service account. `VERTEX_LOCATION` defaults to `us-central1`. |

Both go through the same `google-genai` SDK. Other providers (OpenAI, Anthropic)
are not wired up — [`app/review.py`](app/review.py) is the only file that talks
to a model; a PR is welcome.

---

## Configuration

All via environment variables (see [`.env.example`](.env.example) for the full list).

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_APP_ID` | — | GitHub App ID (required) |
| `GITHUB_APP_PRIVATE_KEY` | — | App private key, PEM (required) |
| `GITHUB_WEBHOOK_SECRET` | — | Webhook HMAC secret (required — service won't start if unset; warns if shorter than 16 chars) |
| `GENAI_API_KEY` | `""` | Gemini API key. If set, used instead of Vertex |
| `GCP_PROJECT` | `""` | GCP project for Vertex AI (required if no API key) |
| `VERTEX_LOCATION` | `us-central1` | Vertex region |
| `MODEL` | `gemini-2.5-flash` | Model id |
| `THINKING_BUDGET` | `2048` | Thinking tokens; `0` = off (cheapest) |
| `REQUEST_TIMEOUT` | `120` | Seconds per model call |
| `ALLOWED_ORGS` | `""` | Comma-separated org logins allowed to use this App. **Empty = every install is served** (and costs you model calls). Case-insensitive |
| `PROMPT_FILE` | `prompt.md` | Review prompt file, relative to repo root |
| `EXCLUDE_GLOBS` | lockfiles, `dist/**`, `node_modules/**`, … | Paths skipped in the diff |
| `MAX_PATCH_CHARS` | `12000` | Per-file diff chars sent to the model |
| `MAX_FILES` | `40` | Max files reviewed per PR |
| `MAX_FINDINGS` | `30` | Max findings posted per review |
| `REVIEW_COMMAND` | `/genai-review` | PR comment that re-triggers a review |
| `SENTRY_DSN` | `""` | Optional. Error reporting; no-op when empty |

### Changing the review prompt

Edit [`prompt.md`](prompt.md), commit, redeploy. If the file is missing, the
built-in [`app/default_prompt.py`](app/default_prompt.py) is used.

---

## Using it on more than one org

An App marked *"Only on this account"* can't be installed elsewhere. To serve a
second org, make the App **public** (App → General → *Make public*; it stays
unlisted) and set `ALLOWED_ORGS` so only your orgs are served — any other
install gets a `204` before a single model call, so a stray public install costs
nothing. Then install from
`https://github.com/apps/<APP_SLUG>/installations/new`.

---

## Security

- **Webhook auth is the HMAC signature.** `X-Hub-Signature-256` is verified with
  a constant-time compare; missing or malformed signatures are rejected. The
  service refuses to start if `GITHUB_WEBHOOK_SECRET` is shorter than 16 chars.
- **The Cloud Run service is `--allow-unauthenticated`** because GitHub can't
  send bearer tokens. The signature is what gates it — keep the secret secret.
- **Cost:** a public App with an empty `ALLOWED_ORGS` will run model calls for
  anyone who installs it. Set `ALLOWED_ORGS`.
- **Prompt injection:** PR diff text is sent to the model as data. Because the
  bot only *comments* (never approves or merges), the worst case is a misleading
  comment. Still, treat its output as advisory.
- **Logs** include model output on parse failure, which can contain snippets of
  the reviewed code. Fine for a private self-hosted instance; be aware if you
  ship logs somewhere shared.
- Report vulnerabilities per [`SECURITY.md`](SECURITY.md).

---

## Limitations

- Reviews the **diff only** — no whole-repo context, no cross-file reasoning
  beyond what's in the patch.
- `MAX_FILES` / `MAX_PATCH_CHARS` cap large PRs; oversized diffs are truncated.
- Background review runs in-process after a `202`. Fine for a handful of repos;
  for high volume, move the job to a queue (Cloud Tasks / Pub-Sub) and have
  `/webhook` only enqueue.
- Single prompt per deployment. No per-repo overrides.
- GitHub Enterprise Server is not supported out of the box (would need a
  configurable API base URL).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Run the self-checks before a PR:

```bash
python -m app.gh          # diff-hunk parser + signature guard
python -m tests.test_config
```

## Cost

Gemini 2.5 Flash, ~5k–30k tokens per PR → typically under US$0.01 per review.
`THINKING_BUDGET=0` makes it cheaper (the resolved-check call already runs with
thinking off). Cloud Run `--min-instances=0` = pay only while a review runs.

## License

[MIT](LICENSE).
