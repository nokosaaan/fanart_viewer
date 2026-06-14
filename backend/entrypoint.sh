#!/bin/sh
set -e

# Wait for Postgres to become available (simple loop)
if [ -n "$DATABASE_HOST" ]; then
  echo "Waiting for postgres at $DATABASE_HOST:$DATABASE_PORT..."
  until pg_isready -h "$DATABASE_HOST" -p "${DATABASE_PORT:-5432}" >/dev/null 2>&1; do
    sleep 1
  done
fi

echo "Running migrations..."
python manage.py migrate --noinput

echo "Starting server..."
if [ "${DJANGO_DEBUG:-1}" = "0" ]; then
  # Production: collect static files then start gunicorn
  echo "Collecting static files..."
  python manage.py collectstatic --noinput 2>/dev/null || true
  exec gunicorn backend.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --timeout 90 \
    --access-logfile - \
    --error-logfile -
else
  # Development: Django runserver with auto-reload
  exec python manage.py runserver 0.0.0.0:8000
fi
