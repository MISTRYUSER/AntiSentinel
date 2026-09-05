# Docker startup validation — 2026-09-05

## Changes

- Added Dockerfile, Compose API/Redis services, named volumes, health checks and environment example.
- Excluded credentials, runtime storage and benchmark datasets from Git and image context.
- Corrected the demo Redis probe to use ANTISENTINEL_REDIS_URL.

## Verification

- `docker compose config --quiet`: passed.
- Image build: passed on local ARM64 Docker Engine.
- Targeted tests: 24 passed.
- `/Users/xuewentao/miniconda3/bin/python3 -m pytest -q tests`: 288 passed, one existing Starlette deprecation warning.
- Isolated Compose project: `antisentinel-docker-check`, localhost port 18765.
- GET /, /dashboard, /api/runtime/config: HTTP 200.
- Demo diagnosis: completed, two successful tool calls against container Redis.
- Forced API container recreation: completed session and successful tool results recovered from persistent storage.
- Modified deployment files passed Git whitespace checks; pre-existing scaffold/docs/vendor whitespace was not rewritten.
- Real external model and optional LibreChat/Phoenix were not exercised.

No production storage or external model credentials were used by the Docker smoke test.
