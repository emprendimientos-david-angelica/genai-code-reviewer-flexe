from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GitHub App
    github_app_id: str
    github_app_private_key: str          # PEM contents; \n-escaped is fine
    github_webhook_secret: str

    # Vertex AI
    gcp_project: str
    vertex_location: str = "us-central1"
    model: str = "gemini-2.5-flash"
    thinking_budget: int = 2048         # 0 disables thinking (cheapest)

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


settings = Settings()  # type: ignore[call-arg]
