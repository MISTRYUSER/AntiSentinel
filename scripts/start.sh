#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop or Docker Engine first." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker and run this script again." >&2
  exit 1
fi

if [ -f .env.local ]; then
  docker compose --env-file .env.local up --build -d --wait
else
  docker compose up --build -d --wait
fi

PORT="${ANTISENTINEL_PORT:-8765}"
echo "AntiSentinel is ready: http://127.0.0.1:${PORT}"
echo "Dashboard: http://127.0.0.1:${PORT}/dashboard"
echo "Logs: docker compose logs -f antisentinel"
