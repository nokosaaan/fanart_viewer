#!/bin/sh
set -e

# Entrypoint for the `poller` docker-compose service (see
# item/management/commands/poll_twitter_updates.py). Separate from
# entrypoint.sh because that script unconditionally execs a web server
# (gunicorn/runserver) at the end — this service runs a different
# long-lived process instead, so it needs its own wait-for-db + migrate
# + exec sequence rather than sharing that script.

if [ "${DB_ENGINE:-postgresql}" != "sqlite3" ] && [ -n "$DATABASE_HOST" ]; then
  echo "poller: waiting for postgres at $DATABASE_HOST:$DATABASE_PORT..."
  until pg_isready -h "$DATABASE_HOST" -p "${DATABASE_PORT:-5432}" >/dev/null 2>&1; do
    sleep 1
  done
fi

echo "poller: running migrations..."
# See entrypoint.sh's own comment: serializes against `web`'s own migrate
# on startup when both share one SQLite file.
if command -v flock >/dev/null 2>&1; then
  mkdir -p /app/data
  flock /app/data/.migrate.lock -c "python manage.py migrate --noinput"
else
  python manage.py migrate --noinput
fi

echo "poller: starting poll_twitter_updates..."
exec python manage.py poll_twitter_updates
