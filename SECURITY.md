# Security

## Reporting

Email t@tonyzhao.com with the details. You'll get a reply within 72 hours. Please don't open a public issue for anything that could expose user data.

## What the CLI sends

Only aggregate numbers: health score, token totals, per-model token/cost split, hour-of-day request histogram, rule hits, cache hit rate, and an anonymous random device token. Never prompt content, file contents, file paths, or account identifiers. See `agentfit/sync.py::build_profile` — that function is the entire upload surface.

## Device token

Each machine generates a random 128-bit token (`uuid4`) stored in `~/.agentfit/config.json`. It's the only key to that machine's data. The web dashboard sends it as an `X-Device-Token` header (not in the URL) so it doesn't land in access logs. Treat the `?t=` link the CLI prints like a password: it binds whoever opens it to your data.

## Backend hardening in place

- Write endpoints are rate-limited per IP.
- All read endpoints require a device token; there is no "latest global" endpoint.
- Operational endpoints (export, content pool management, billing status) require an `X-API-Key` that must be explicitly configured — the default key is rejected.
- The service refuses to start on Railway without a real `DATABASE_URL`, so data can't silently land on an ephemeral disk.
