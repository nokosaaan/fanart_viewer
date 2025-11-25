#!/bin/sh
set -e

# Wait for Postgres to become available (simple loop)
# If DATABASE_URL is provided (Render), parse host/port so the wait loop can use them.
if [ -n "$DATABASE_URL" ] && [ -z "$DATABASE_HOST" ]; then
  echo "Parsing DATABASE_URL to obtain host/port..."
  HOST_PORT=$(python3 - <<'PY'
import os
from urllib.parse import urlparse
u = urlparse(os.environ.get('DATABASE_URL',''))
host = u.hostname or ''
port = u.port or ''
print(f"{host}:{port}")
PY
)
  export DATABASE_HOST=$(echo "$HOST_PORT" | cut -d: -f1)
  export DATABASE_PORT=$(echo "$HOST_PORT" | cut -d: -f2)
fi

if [ -n "$DATABASE_HOST" ]; then
  echo "Waiting for postgres at $DATABASE_HOST:$DATABASE_PORT..."
  until pg_isready -h "$DATABASE_HOST" -p "${DATABASE_PORT:-5432}" >/dev/null 2>&1; do
    sleep 1
  done
fi

# Only run migrations/import if we have DB connection info available.
if [ -n "$DATABASE_URL" ] || [ -n "$POSTGRES_DB" ]; then
  echo "Making migrations (if needed)..."
  python manage.py makemigrations --noinput || true

  echo "Running migrations..."
  python manage.py migrate --noinput

  echo "Importing JSON data (idempotent)..."
  python manage.py import_json_data || true
else
  echo "No DATABASE_URL or POSTGRES_DB found — skipping migrations and import."
fi

echo "Collecting static files..."
python manage.py collectstatic --noinput || true

echo "Starting Gunicorn..."
# Use 1 worker per CPU core, capped to a sensible range. Fallback to 3 workers.
WORKERS=${GUNICORN_WORKERS:-3}
THREADS=${GUNICORN_THREADS:-4}
# Bind to the port provided by the environment (Render provides $PORT).
BIND_PORT=${PORT:-8000}
echo "Binding to 0.0.0.0:${BIND_PORT} (PORT=${PORT:-not-set})"
exec gunicorn backend.wsgi:application \
  --bind 0.0.0.0:${BIND_PORT} \
  --workers "$WORKERS" \
  --threads "$THREADS" \
  --log-level info
