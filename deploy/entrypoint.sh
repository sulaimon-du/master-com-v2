#!/bin/sh
set -eu
# With arguments, run them instead of the web app, before migrations and seed: the bot service
# starts as `entrypoint.sh python -m app.bot` (compose `command`, Render `dockerCommand`)
# and never touches the schema or reference data.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
# Staging runs migrations on start (free hosting has no pre-deploy step).
# Production runs them as a separate one-off command before switching traffic.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi
# Reference data (cities, districts, catalog). Idempotent, so it is safe on every start.
if [ "${RUN_SEED:-0}" = "1" ]; then
  python -m app.seed
fi
exec uvicorn --factory app.main:create_app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --no-server-header
