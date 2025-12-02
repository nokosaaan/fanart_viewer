# Fanart Viewer — Local release: Install & run from scratch

This document explains how to install and run a local release of Fanart Viewer
from a clean machine (no existing JSON fixture, no DB migrations applied).
It uses Docker and Docker Compose to provide a reproducible environment.

Prerequisites
 - Docker (engine) installed and working
 - Docker Compose (v2 or later) or `docker compose` plugin
 - git (to clone the repository)
 - At least ~1.5–4 GB free disk and a few GB RAM (backend image with headless browser may be large)

Overview
 - The repository includes a `docker-compose.yml` with three services:
   - `db`: PostgreSQL 15
   - `web`: Django backend (runs migrations and gunicorn via `entrypoint.sh`)
   - `frontend`: React/Vite frontend (dev server in local compose)

High-level steps
 1. Clone repo and create `.env` from `.env.example`
 2. Configure required environment variables (DB password, optional API keys)
 3. Start services with Docker Compose
 4. Run Django migrations and create a superuser
 5. (Optional) Import JSON fixtures when you have them
 6. Access the app and admin UI

Step-by-step

1) Clone the repository

```bash
git clone https://github.com/nokosaaan/fanart_viewer.git
cd fanart_viewer
git checkout local  # optional: if you want the local branch
```

2) Create `.env` and set secrets

Copy the example `.env` and the backend example into a working `.env`. The compose file reads `.env` in the project root and also the backend service sources `backend/.env` via the volume.

```bash
cp .env.example .env
cp backend/.env.example backend/.env

# Edit .env and backend/.env to set at least:
# - POSTGRES_PASSWORD  (strong password)
# - TW_BEARER (optional; required for Twitter API based media fetches)
# - DJANGO_SECRET_KEY (backend/.env)
# - DJANGO_DEBUG=1 (for local development; set to 0 in production)

editor .env backend/.env
```

Important environment variables (minimum)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — database credentials
- `DJANGO_SECRET_KEY` — Django secret key
- `DJANGO_DEBUG` — set `1` for local development
- `VITE_BACKEND_URL` or `BACKEND_URL` — if running frontend separately

3) Build and start services

For a first run you can build the images and start services:

```bash
# Build and start (detached)
docker compose build
docker compose up -d

# View logs
docker compose logs -f web
```

Notes:
- The `web` service uses `backend/entrypoint.sh` which waits for the DB and runs migrations automatically in some setups. If your setup mounts the code, you may need to run migrations manually (see next step).

4) Run migrations and create an admin user

If the entrypoint did not run migrations, run them manually against the running `web` container:

```bash
# run migrations
docker compose exec web python manage.py migrate --noinput

# create a Django superuser (interactive)
docker compose exec web python manage.py createsuperuser
```

If `docker compose exec` reports the container is not ready, check `docker compose logs web` and wait for DB readiness.

5) (Optional) Import data when you have a fixture

If you later obtain a JSON fixture (Django `dumpdata` format), you can import it using the included management command. Place the fixture on the host and use a command like:

```bash
# copy fixture into container (example)
cp /path/to/items-backup.json backend/backup/items-backup.json

# then run import inside web container
docker compose exec web python manage.py import_json_data /app/backup/items-backup.json
```

Notes about large backups and previews
- If your JSON is large, compress it (`.gz` or `.zip`) before using the `restore` UI — the backend supports `.gz` and `.zip` uploads and will extract the JSON server-side.
- The repo includes an admin-only endpoint to upload a fixture and run `restore_previews_from_fixture`. To use that endpoint locally you may set the env var `RESTORE_PREVIEWS_PASSWORD` and then use the frontend UI or `curl` to POST the file.

Example: gzip and upload via curl (dry-run)

```bash
# compress (if large)
gzip -c items-backup.json > items-backup.json.gz

# POST to local web (CORS not needed when same host)
curl -v -F "file=@items-backup.json.gz" -F "password=your_password" -F "dry_run=1" http://localhost:8000/api/admin/restore_previews/
```

6) Access the app

- Frontend (dev server): http://localhost:3000
- Backend API: http://localhost:8000/api/
- Django admin: http://localhost:8000/admin/ (login with the superuser you created)

Troubleshooting
- If the web container exits with database errors, check `docker compose logs db` and `docker compose logs web` for hints. The DB may not be accepting connections yet — the entrypoint waits but if you ran migrations manually you may need to retry.
- If uploads are rejected (413 Request Entity Too Large), and you're running locally with the compose nginx/dev server, check whether the platform proxy or reverse-proxy (if any) limits uploads; locally with the plain compose above you should not hit platform limits. Use compressed upload as a workaround.

Production notes (brief)
- For production packaging consider:
  - Building frontend into static files and serving with nginx (production `frontend/Dockerfile` supports this)
  - Using a managed Postgres (Render, RDS) and configuring `DATABASE_URL`
  - Adding TLS and secure env var storage

FAQ / common commands

Start (build if needed):

```bash
docker compose up -d --build
```

Stop and remove containers:

```bash
docker compose down
```

Run a shell in the web container:

```bash
docker compose exec web /bin/bash
```

Help and next steps
- If you want, I can also:
  - Add `docker-compose.prod.yml` for an opinionated production setup
  - Add a small `RELEASE` script to automate migration + createsuperuser + sample data import
  - Add a GitHub Actions workflow to build and push Docker images automatically

If you'd like any of those, tell me which and I will prepare the files.

---
Last updated: 2025-12-03
