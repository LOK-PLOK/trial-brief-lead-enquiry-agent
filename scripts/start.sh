#!/bin/sh
# Production entrypoint for the single-container image (see root Dockerfile).
# Render (and most PaaS hosts) inject PORT at runtime; default to 8000 for
# local `docker run` without that env var. Single uvicorn worker intentionally
# — SQLite + trial-scale traffic (docs/architecture.md section 13 / 15).
set -eu
PORT="${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
