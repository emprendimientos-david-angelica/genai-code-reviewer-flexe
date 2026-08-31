# genai-code-reviewer-flexe

GitHub App that reviews pull requests with **Gemini 2.5 Flash** on Vertex AI.

- Inline comments on the changed lines, one per finding, with severity and
  optional `suggestion` blocks.
- One summary review comment at the end of the PR (counts, overall notes,
  findings that landed outside the diff).
- Re-validation: on every push to the PR (`synchronize`) and on the
  `/genai-review` comment, it re-reviews the current diff **and** re-checks the
  previous findings, marking each ✅ resolved / ❌ open / ❔ unknown.
- **One org-wide prompt** — [`prompt.md`](prompt.md) in this repo. Change it,
  push to `main`, it redeploys. No per-repo config.
- **No database.** Prior findings are read back from the App's own review
  comments on the PR.

## Architecture

```
GitHub PR event ──HTTPS──▶ Cloud Run: genai-code-reviewer-flexe (FastAPI /webhook)
                              │  verify HMAC signature
                              │  installation token (GitHub App)
                              │  GET PR files + patches
                              ├─▶ Gemini 2.5 Flash: diff → findings JSON
                              ├─▶ Gemini 2.5 Flash: prior findings → resolved?
                              └─▶ POST PR review: inline comments + summary
```

Deploy mirrors `la-secre-bk`: push to `main` → GitHub Actions builds the image,
pushes to Artifact Registry `lasecre-repo` (us-east1), deploys Cloud Run.

---

## Part A — create the GitHub App  (you do this, ~5 min)

GitHub can't create Apps via API, so this part is manual.

1. Go to **your org → Settings → Developer settings → GitHub Apps → New GitHub App**
   (URL: `https://github.com/organizations/<ORG>/settings/apps/new`).
2. Fill in:
   - **GitHub App name**: `genai-code-reviewer-flexe`
   - **Homepage URL**: your org URL (anything valid)
   - **Webhook → Active**: ✅ checked
   - **Webhook URL**: `https://PLACEHOLDER/webhook` (fixed after first deploy)
   - **Webhook secret**: generate a random string, **keep it** → this is
     `WEBHOOK_SECRET` below.
3. **Repository permissions**:
   - **Pull requests**: Read and write
   - **Metadata**: Read-only (auto)
   - everything else: No access
4. **Subscribe to events**: ✅ **Pull request**, ✅ **Issue comment**
5. **Where can this app be installed?**: *Only on this account*.
6. Click **Create GitHub App**.
7. On the App page, note the **App ID**.
8. Scroll to **Private keys → Generate a private key** → downloads a `.pem`. Keep it.
9. **Do not install it yet** — do that in Part D, on the important repos only.

You now have: **App ID**, **`.pem` file**, **webhook secret**. Send me the App
ID; put the `.pem` and secret where Part C says (they go to GCP Secret Manager,
never to GitHub).

---

## Part B — push this code to a new repo  (you do this)

```bash
cd ai-pr-reviewer
git init && git add . && git commit -m "genai-code-reviewer-flexe: initial"
gh repo create <ORG>/genai-code-reviewer-flexe --private --source=. --push
```

---

## Part C — GCP + GitHub secrets  (I run this)

I create, in project `proyectos-david-y-angelica` (region `us-east1`):

| Resource | Name |
|---|---|
| Deploy SA (GitHub Actions identity) | `genai-reviewer-deploy@…` — roles: `run.admin`, `artifactregistry.writer`, `iam.serviceAccountUser` |
| Runtime SA (Cloud Run identity) | `genai-reviewer-runtime@…` — roles: `aiplatform.user`, `secretmanager.secretAccessor` |
| Secret Manager | `genai-reviewer-gh-private-key` ← your `.pem` |
| Secret Manager | `genai-reviewer-webhook-secret` ← your webhook secret |

Then the repo's **Actions secrets** (only 3, none is the App private key):

| Secret | Value |
|---|---|
| `GCP_PROJECT_ID` | `proyectos-david-y-angelica` |
| `GCP_SA_KEY` | JSON key of `genai-reviewer-deploy` |
| `GH_APP_ID` | your App ID |

---

## Part D — first deploy + wire the webhook

1. Push to `main` (or run the **Deploy** workflow manually). The workflow's
   summary prints the **Webhook URL**
   (`https://genai-code-reviewer-flexe-…run.app/webhook`).
2. GitHub App → **General → Webhook URL** → replace the placeholder with that
   URL → Save.
3. GitHub App → **Install App** → your org → **Only select repositories** → pick
   the important repos → Install.
4. Open a PR on one of them. The review posts within a minute.

Add or remove repos any time from the App's **Install App** page — that is the
on/off switch, per repo.

### Using the App on more than one org

The App is *"Only on this account"*, so a second org can't install it as-is.
Make it public (App → **General → Make public**; stays unlisted) and set the
cost guard so only your orgs are served:

- Repo → **Settings → Secrets and variables → Actions → Variables** →
  `ALLOWED_ORGS` = comma-separated org logins, e.g. `flexe-org,my-other-org`.
- Redeploy. Any install outside that list gets a `204` before a single Gemini
  call — a stray public install costs nothing.
- Empty / unset `ALLOWED_ORGS` = serve every installation (original behaviour).

Then install on each org from `https://github.com/apps/<APP_SLUG>/installations/new`.

---

## Changing the review prompt

Edit [`prompt.md`](prompt.md), commit, push to `main`. Redeploy is automatic.
Same for `EXCLUDE_GLOBS` / `MAX_FINDINGS` — set them as env vars in
`.github/workflows/deploy.yml` (`--set-env-vars`).

## Local dev

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill values; gcloud auth application-default login for Vertex
uvicorn app.main:app --reload --port 8080
python app/gh.py              # self-check: diff-hunk parser + signature guard
```

Use a tunnel (`cloudflared tunnel --url http://localhost:8080`) as the webhook
URL while iterating.

## Cost

Gemini 2.5 Flash, ~5k–30k tokens per PR → typically under US$0.01 per review.
`THINKING_BUDGET=0` makes it cheaper; the resolved-check call already runs with
thinking off. Cloud Run `--min-instances=0` = pay only while a review runs.

## Scaling ceiling

`BackgroundTasks` runs the review in-process after a `202`. Fine for a handful
of repos. Higher volume → push the job to Cloud Tasks / Pub-Sub and have
`/webhook` only enqueue.
