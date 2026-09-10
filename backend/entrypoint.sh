#!/bin/sh
set -e

# Wait for Postgres to become available (simple loop) — skipped entirely in
# SQLite mode (DB_ENGINE=sqlite3), where there's no separate DB server to
# wait for and the `db` service isn't even started (see docker-compose.prod.yml,
# where `db` is behind the `postgres` profile).
if [ "${DB_ENGINE:-postgresql}" != "sqlite3" ] && [ -n "$DATABASE_HOST" ]; then
  echo "Waiting for postgres at $DATABASE_HOST:$DATABASE_PORT..."
  until pg_isready -h "$DATABASE_HOST" -p "${DATABASE_PORT:-5432}" >/dev/null 2>&1; do
    sleep 1
  done
fi

echo "Running migrations..."
# In SQLite mode, `web` and `poller` are two separate containers racing to
# migrate the same file on startup — serialize them via a lock file on the
# shared /app/data volume (both mount it) so one waits for the other
# instead of both issuing concurrent DDL against one SQLite file. A no-op
# in Postgres mode: DATA_DIR is /app/data regardless, and flock around a
# few seconds of `migrate` costs nothing when it isn't even contended.
if command -v flock >/dev/null 2>&1; then
  mkdir -p /app/data
  flock /app/data/.migrate.lock -c "python manage.py migrate --noinput"
else
  python manage.py migrate --noinput
fi

echo "Starting server..."
if [ "${DJANGO_DEBUG:-1}" = "0" ]; then
  # Production: collect static files then start gunicorn
  echo "Collecting static files..."
  python manage.py collectstatic --noinput 2>/dev/null || true
  # Single-user personal-archive app — 1 worker is plenty, and in SQLite
  # mode it also halves the number of processes contending for the same
  # file's write lock (the other being `poller`). Bump via GUNICORN_WORKERS
  # in .env if ever needed (e.g. staying on Postgres, multiple viewers).
  exec gunicorn backend.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-1}" \
    --timeout "${GUNICORN_TIMEOUT:-90}" \
    --access-logfile - \
    --error-logfile -
else
  # Development: Django runserver with auto-reload
  exec python manage.py runserver 0.0.0.0:8000
fi
