#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-4173}"
CONDA_PYTHON="/Users/bagdaemin/opt/miniconda3/envs/jb/bin/python"

if [[ -n "${LTV_BACKEND_PYTHON:-}" ]]; then
  PYTHON_BIN="$LTV_BACKEND_PYTHON"
elif [[ -x "$CONDA_PYTHON" ]]; then
  PYTHON_BIN="$CONDA_PYTHON"
else
  PYTHON_BIN="python3"
fi

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then kill "$BACKEND_PID" >/dev/null 2>&1 || true; fi
  if [[ -n "${FRONTEND_PID:-}" ]]; then kill "$FRONTEND_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM

echo "Building frontend with local production API target"
cd "$ROOT_DIR/frontend"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}" npm run build

echo "Starting LTV local production preview"
echo "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "Frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}"

cd "$ROOT_DIR"
"$PYTHON_BIN" -m uvicorn backend.main:app \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$ROOT_DIR/frontend"
npm run preview -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
