#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ANTISENTINEL_ENV_FILE:-$PROJECT_ROOT/.env.local}"

cd "$PROJECT_ROOT"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PYTHONPATH="${PYTHONPATH:-$PROJECT_ROOT/src}"
export ANTISENTINEL_MODEL_MODE="${ANTISENTINEL_MODEL_MODE:-real}"
export ANTISENTINEL_MODEL_BASE_URL="${ANTISENTINEL_MODEL_BASE_URL:-https://api.openai.com/v1}"
export ANTISENTINEL_MODEL_NAME="${ANTISENTINEL_MODEL_NAME:-gpt-4o-mini}"
export ANTISENTINEL_MEMORY_MODEL_BASE_URL="${ANTISENTINEL_MEMORY_MODEL_BASE_URL:-$ANTISENTINEL_MODEL_BASE_URL}"
export ANTISENTINEL_MEMORY_MODEL_NAME="${ANTISENTINEL_MEMORY_MODEL_NAME:-$ANTISENTINEL_MODEL_NAME}"
export ANTISENTINEL_MODEL_TIMEOUT_SECONDS="${ANTISENTINEL_MODEL_TIMEOUT_SECONDS:-30}"
export ANTISENTINEL_STORAGE_ROOT="${ANTISENTINEL_STORAGE_ROOT:-$PROJECT_ROOT/storage}"
export ANTISENTINEL_PERSISTENCE_MODE="${ANTISENTINEL_PERSISTENCE_MODE:-sqlite}"
export ANTISENTINEL_SQLITE_PATH="${ANTISENTINEL_SQLITE_PATH:-$ANTISENTINEL_STORAGE_ROOT/antisentinel.db}"
export ANTISENTINEL_REDIS_URL="${ANTISENTINEL_REDIS_URL:-redis://127.0.0.1:6379/0}"
# LibreChat runs in Docker and reaches the host through host.docker.internal.
# Keep the published port local while binding all interfaces inside the host
# network namespace so the container bridge can reach it.
export ANTISENTINEL_HOST="${ANTISENTINEL_HOST:-0.0.0.0}"
export ANTISENTINEL_PORT="${ANTISENTINEL_PORT:-8765}"

if [[ "${ANTISENTINEL_ENABLE_PHOENIX:-0}" == "1" ]]; then
  export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://127.0.0.1:4317}"
else
  unset OTEL_EXPORTER_OTLP_ENDPOINT
fi

if [[ "$ANTISENTINEL_MODEL_MODE" == "real" && -z "${ANTISENTINEL_MODEL_API_KEY:-}" ]]; then
  echo "Missing ANTISENTINEL_MODEL_API_KEY in $ENV_FILE" >&2
  exit 1
fi

REDIS_HOST="127.0.0.1"
REDIS_PORT="6379"
if [[ "$ANTISENTINEL_REDIS_URL" =~ redis://[^:]+:([0-9]+)/ ]]; then
  REDIS_PORT="${BASH_REMATCH[1]}"
fi

if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  redis-server --port "$REDIS_PORT" --daemonize yes --appendonly yes
fi

if [[ "${ANTISENTINEL_START_LIBRECHAT:-0}" == "1" ]]; then
  docker compose -f "$PROJECT_ROOT/docker-compose.oss.yml" --env-file "$PROJECT_ROOT/deploy/.env.oss.example" up -d librechat librechat-mongodb librechat-meilisearch
fi

if [[ "${ANTISENTINEL_START_PHOENIX:-0}" == "1" ]]; then
  docker compose -f "$PROJECT_ROOT/docker-compose.oss.yml" --env-file "$PROJECT_ROOT/deploy/.env.oss.example" up -d phoenix phoenix-db
fi

if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  echo "Redis is not reachable at $REDIS_HOST:$REDIS_PORT" >&2
  exit 1
fi

echo "AntiSentinel starting on http://$ANTISENTINEL_HOST:$ANTISENTINEL_PORT"
echo "Redis: $ANTISENTINEL_REDIS_URL"
echo "SQLite: $ANTISENTINEL_SQLITE_PATH ($ANTISENTINEL_PERSISTENCE_MODE)"
echo "Model: $ANTISENTINEL_MODEL_BASE_URL / $ANTISENTINEL_MODEL_NAME"
echo "Phoenix OTLP: ${OTEL_EXPORTER_OTLP_ENDPOINT:-disabled}"
echo "LibreChat: ${ANTISENTINEL_START_LIBRECHAT:-0}"

CODE_MAP_PID=""
API_PID=""
cleanup() {
  if [[ -n "$CODE_MAP_PID" ]]; then kill "$CODE_MAP_PID" 2>/dev/null || true; fi
  if [[ -n "$API_PID" ]]; then kill "$API_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

if [[ "${ANTISENTINEL_CODE_MAP_ENABLED:-0}" == "1" ]]; then
  python -m antisentinel.code_map &
  CODE_MAP_PID=$!
fi
uvicorn antisentinel.api.app:app --host "$ANTISENTINEL_HOST" --port "$ANTISENTINEL_PORT" &
API_PID=$!
wait "$API_PID"
