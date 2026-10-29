#!/usr/bin/env bash
# Build the React app and serve the whole product (SPA + API) from one port.
# Usage: ./run_live.sh            -> http://localhost:5000
#        PORT=8080 ./run_live.sh  -> http://localhost:8080
set -euo pipefail
cd "$(dirname "$0")"

export PORT="${PORT:-5000}"
PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv"
  "$PY" -m venv .venv
fi
echo "==> Installing backend dependencies"
.venv/bin/pip install -q -r requirements.txt

[ -f .env ] || { echo "==> Creating .env from .env.example"; cp .env.example .env; }

if [ ! -d frontend/node_modules ]; then
  echo "==> Installing frontend dependencies"
  (cd frontend && npm install --no-audit --no-fund)
fi

echo "==> Building frontend"
(cd frontend && npm run build)

echo "==> Seeding demo data (no-op if the database already has records)"
.venv/bin/python -m backend.seed

echo "==> Roam is starting on http://localhost:${PORT}"
exec .venv/bin/python -m backend.app
