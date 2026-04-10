# Security Notes

This repository is designed for local telemetry demos. Before publishing or sharing, verify the controls below.

## Secrets and environment files

- Commit only `.env.example` with placeholder values.
- Keep `.env` local-only and never commit real secrets.
- Required runtime secrets (`DJANGO_SECRET_KEY`, `INGEST_API_KEY`) reject placeholder prefixes like `REPLACE_ME` and `changeme`.

## Local network exposure defaults

- `docker-compose.yml` binds Postgres and Django ports to `127.0.0.1` by default.
- This keeps local demo credentials from being exposed on non-localhost interfaces.

## Telemetry logging policy

- Console and app logs are metadata-first.
- The console backend logs identifiers and attribute keys, not full span payload bodies.
- Demo agent completion logs include output length and identifiers, not final prompt/response content.

## Secret scanning controls

- A Gitleaks scan runs in CI (`.github/workflows/ci.yml`).
- A pre-commit hook config is included (`.pre-commit-config.yaml`) to catch local leaks before commit.

## Redaction expectations

- Span attributes can still contain LLM/tool content when you choose to instrument it.
- Do not run this demo with production secrets, customer PII, or regulated data.
