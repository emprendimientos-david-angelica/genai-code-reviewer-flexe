# Security Policy

## Reporting a vulnerability

Use this repository's **Security → Advisories → Report a vulnerability**, or
email the maintainer. Please do **not** open a public issue for anything
exploitable.

Expect an acknowledgement within a few days.

## Scope

This is self-hosted software. Each operator runs their own instance with their
own GitHub App, secrets, and LLM credentials. Relevant classes of issue:

- Webhook signature bypass or forgery.
- Leaking installation tokens, the App private key, or the webhook secret.
- Making the service perform unbounded model calls (cost abuse).
- Prompt injection that causes the bot to do more than post a comment.

## Operator checklist

- `GITHUB_WEBHOOK_SECRET` is long and random. The service refuses to start with
  a secret shorter than 16 chars.
- `ALLOWED_ORGS` is set unless the instance is deliberately open to all.
- The App private key and webhook secret live in a secret manager, never in the
  image or a committed file (`.env` and `*.pem` are gitignored).
- Consider pinning GitHub Actions to commit SHAs rather than tags.
- Consider Workload Identity Federation instead of a long-lived `GCP_SA_KEY`.
