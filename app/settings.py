from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GitHub App
    github_app_id: str
    github_app_private_key: str          # PEM contents; \n-escaped is fine
    github_webhook_secret: str

    # LLM backend. Two ways to reach Gemini, pick one:
    #  - Gemini API key (simplest, deploy anywhere): set genai_api_key.
    #  - Vertex AI (no key, uses ADC / the runtime service account): leave
    #    genai_api_key empty and set gcp_project.
    genai_api_key: str = ""
    gcp_project: str = ""
    vertex_location: str = "us-central1"
    model: str = "gemini-2.5-flash"
    thinking_budget: int = 2048         # 0 disables thinking (cheapest)
    request_timeout: int = 120          # seconds per model call

    # One org-wide review prompt. Edit prompt.md in this repo and redeploy.
    prompt_file: str = "prompt.md"

    # Org-wide path excludes (comma-separated globs)
    exclude_globs: str = "**/*.lock,**/*.min.js,**/*.snap,dist/**,build/**,vendor/**,node_modules/**"

    # Monitoring (optional — no-op when sentry_dsn is empty)
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    environment: str = "production"

    # Limits (cost control)
    max_patch_chars: int = 12000        # per-file diff chars sent to the model
    max_files: int = 40
    max_findings: int = 30

    review_command: str = "/genai-review"  # comment on a PR to re-run

    # Cost guard: comma-separated org logins allowed to use this App.
    # Empty = allow every installation (back-compat). Any other install is ignored
    # before a single model call, so a stray public install costs nothing.
    allowed_orgs: str = ""

    @property
    def private_key(self) -> str:
        return self.github_app_private_key.replace("\\n", "\n")

    @property
    def allowed_org_set(self) -> set[str]:
        return {o.strip().lower() for o in self.allowed_orgs.split(",") if o.strip()}

    @property
    def exclude_list(self) -> list[str]:
        return [g.strip() for g in self.exclude_globs.split(",") if g.strip()]

    @property
    def prompt(self) -> str:
        path = _ROOT / self.prompt_file
        if path.is_file():
            return path.read_text(encoding="utf-8")
        from .default_prompt import DEFAULT_PROMPT

        return DEFAULT_PROMPT

    def check(self) -> list[str]:
        """Fail fast on misconfig; return non-fatal warnings for the caller to log."""
        if not self.github_webhook_secret:
            raise RuntimeError(
                "GITHUB_WEBHOOK_SECRET is not set. The webhook signature check "
                "cannot work without it."
            )
        if not self.genai_api_key and not self.gcp_project:
            raise RuntimeError(
                "No LLM backend configured: set GENAI_API_KEY, or set GCP_PROJECT "
                "to use Vertex AI."
            )
        warnings = []
        if len(self.github_webhook_secret) < 16:
            warnings.append(
                "GITHUB_WEBHOOK_SECRET is shorter than 16 chars; use a longer "
                "random string for a stronger HMAC."
            )
        if not self.allowed_org_set:
            warnings.append(
                "ALLOWED_ORGS is empty: every org that installs this App is served "
                "and its PRs cost you model calls. Set ALLOWED_ORGS unless this "
                "instance is meant to be open to all."
            )
        return warnings


settings = Settings()  # type: ignore[call-arg]
