#!/bin/sh
set -eu

if [ -n "${DATABASE_URL_MIGRATION:-}" ]; then
  export DATABASE_URL="${DATABASE_URL_MIGRATION}"
fi

echo "Running database migrations..."
python3 scripts/bootstrap_migrations.py

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
