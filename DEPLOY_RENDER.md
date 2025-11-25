# Deploying to Render.com (step-by-step)

This file contains step-by-step instructions to deploy the full stack (frontend + backend + managed Postgres) to Render using the `render.yaml` in this repository.

1) Create a Render account and connect your GitHub repository.

2) In the Render dashboard, create a new Web Service and choose "Deploy from a repo". When asked for a `render.yaml`, Render will detect and use the `render.yaml` at the repo root. You can also create services manually and point their Dockerfile paths to the repo paths below.

3) Service configuration (the `render.yaml` in this repo):
   - Backend service: uses `backend/Dockerfile` (Docker environment). It will build the backend image and run the container.
   - Frontend service: uses `frontend/Dockerfile` (multi-stage: builds static site and serves with nginx).
   - Managed Postgres: `fanart-viewer-db` created by the `databases` section.

4) Required secrets and environment variables (set these in Render dashboard > Service > Environment > Environment Variables / Secrets):
   - `DJANGO_SECRET_KEY` (secret): set a secure random value.
   - `DJANGO_DEBUG` (env): set to `0`.
   - (Optional) `POSTGRES_*` variables if not using the managed DB; otherwise Render will expose `DATABASE_URL` to the service.
   - `VITE_BACKEND_URL`: set to the public backend URL (e.g. `https://fanart-viewer-backend.onrender.com`) so the frontend knows where to call the API.

5) Backend notes:
   - The backend image uses Playwright and may be large. Choose a plan that supports larger images.
   - `entrypoint.sh` runs migrations and an import step on startup. This is safe for first deploys.
   - Consider switching from `runserver` to `gunicorn` for production (modify the backend `Dockerfile` entrypoint or Render start command).

6) Frontend notes:
   - The `frontend/Dockerfile` builds the site and serves it with nginx on port 80.
   - After deployment, set `VITE_BACKEND_URL` in the frontend service environment to the backend service URL.

7) CI / Preview Deploys:
   - This repo includes a GitHub Actions workflow `.github/workflows/ci.yml` that builds the frontend and validates the Dockerfiles on push/PR.
   - Render can auto-deploy on merges to `main` if you enable Auto-Deploy in the service settings.

8) Hardening and sharing:
   - Add `DJANGO_DEBUG=0` and set a secure `DJANGO_SECRET_KEY` before going public.
   - Use Render's built-in TLS and domain options, or set a custom domain.
   - Protect the site using basic auth, Cloudflare Access, or Render's internal access controls if you want to limit visibility.

If you'd like, I can:
- Modify the backend `entrypoint.sh` to use `gunicorn` instead of `runserver` (recommended).
- Add an example Render secret template that you can paste into the Render dashboard.
- Create a GitHub Actions deploy workflow to call Render's REST API to trigger deploys (requires Render API key).
