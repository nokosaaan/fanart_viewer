#!/bin/sh
#!/bin/sh
set -e

# Diagnostic: print a masked DATABASE_URL and whether PGPASSWORD is set.
# This is safe for logs because the password is replaced with '***'.
echo "Diagnostic: masking DATABASE_URL for debug"
python3 - <<'PY'
import os
from urllib.parse import urlparse, urlunparse
u = os.environ.get('DATABASE_URL')
if not u:
  print('DATABASE_URL: <not-set>')
else:
  try:
    p = urlparse(u)
    if p.username:
      # mask the password portion if present
      user = p.username
      netloc = p.hostname or ''
      if p.port:
        netloc = f"{netloc}:{p.port}"
      if p.password:
        userinfo = f"{user}:***@"
      else:
        userinfo = f"{user}@"
      masked = urlunparse((p.scheme, userinfo + netloc, p.path or '', p.params or '', p.query or '', p.fragment or ''))
      print(f'DATABASE_URL: {masked}')
    else:
      print('DATABASE_URL: <no-username-present>')
  except Exception as e:
    print('DATABASE_URL: <parse-error>', str(e))
PY
if [ -n "${PGPASSWORD:-}" ]; then
  echo "PGPASSWORD: <set>"
else
  echo "PGPASSWORD: <not-set>"
fi

# Optional: restore previews from fixture after migrations
# Control with env vars: RESTORE_PREVIEWS=1 to enable dry-run; set RESTORE_PREVIEWS_FORCE=1 to apply.
RESTORE_FIXTURE_PATH=${RESTORE_FIXTURE_PATH:-/app/backup/items-backup.json}
if [ "${RESTORE_PREVIEWS:-0}" = "1" ]; then
  echo "RESTORE_PREVIEWS enabled. Fixture path: ${RESTORE_FIXTURE_PATH}"
  # marker prevents repeated application within same container lifecycle
  MARKER=/tmp/.previews_restored
  if [ -f "$MARKER" ]; then
    echo "Preview restore already applied (marker exists: $MARKER). Skipping."
  else
    echo "Running preview restore dry-run (no DB writes) to show what would be done..."
    python manage.py restore_previews_from_fixture "$RESTORE_FIXTURE_PATH" --dry-run -v 2 || true
    if [ "${RESTORE_PREVIEWS_FORCE:-0}" = "1" ]; then
      echo "RESTORE_PREVIEWS_FORCE=1 set — applying preview restore now..."
      if python manage.py restore_previews_from_fixture "$RESTORE_FIXTURE_PATH" -v 2; then
        echo "Preview restore applied successfully. Creating marker $MARKER"
        touch "$MARKER"
      else
        echo "ERROR: Preview restore failed during apply. Check logs above." >&2
      fi
    else
      echo "Dry-run finished. Set RESTORE_PREVIEWS_FORCE=1 to actually apply the restore."
    fi
  fi
fi

# If Render provided a PORT, start a lightweight temporary server early so
# Render's port scanner sees an open port while migrations/import run.
if [ -n "${PORT:-}" ]; then
  echo "Starting temporary bind server on port ${PORT} to satisfy platform port scan..."
  (cd /tmp && python3 -m http.server "${PORT}" --bind 0.0.0.0) >/dev/null 2>&1 &
  TEMP_BIND_PID=$!
  echo "Temporary bind server PID=${TEMP_BIND_PID}"

  # Ensure the temporary server is killed when this script exits for any reason.
  _cleanup() {
    if [ -n "${TEMP_BIND_PID:-}" ]; then
      echo "Cleaning up temporary bind server PID=${TEMP_BIND_PID}"
      kill "${TEMP_BIND_PID}" >/dev/null 2>&1 || true
    fi
  }
  trap _cleanup EXIT INT TERM
fi

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
  # Allow migrate to fail without leaving the temporary server running.
  python manage.py migrate --noinput || {
    echo "Warning: migrate failed. Continuing startup to allow inspection of container logs.";
  }

  echo "Importing JSON data (idempotent)..."
  python manage.py import_json_data || echo "import_json_data failed (non-fatal)"
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
if [ -n "${TEMP_BIND_PID:-}" ]; then
  echo "Stopping temporary bind server PID=${TEMP_BIND_PID}"
  kill ${TEMP_BIND_PID} || true
  # give it a moment to release the port
  sleep 1
fi

exec gunicorn backend.wsgi:application \
  --bind 0.0.0.0:${BIND_PORT} \
  --workers "$WORKERS" \
  --threads "$THREADS" \
  --log-level info
