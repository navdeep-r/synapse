#!/usr/bin/env bash
# Start the Synapse v2 backend. Creates the venv on first run.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv (Python 3.12)..."
  uv venv --python 3.12
  uv pip install -e ".[dev]"
fi

HOST="${SYNAPSE_HOST:-127.0.0.1}"
PORT="${SYNAPSE_PORT:-8001}"

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
