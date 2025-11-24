#!/bin/sh
set -e

# Wait for Postgres to become available (simple loop)
if [ -n "$DATABASE_HOST" ]; then
  echo "Waiting for postgres at $DATABASE_HOST:$DATABASE_PORT..."
  until pg_isready -h "$DATABASE_HOST" -p "${DATABASE_PORT:-5432}" >/dev/null 2>&1; do
    sleep 1
  done
fi

echo "Making migrations (if needed)..."
python manage.py makemigrations --noinput || true

echo "Running migrations..."
python manage.py migrate --noinput

echo "Importing JSON data (idempotent)..."
python manage.py import_json_data || true

echo "Starting server..."
exec python manage.py runserver 0.0.0.0:8000
