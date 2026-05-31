# fanart_viewer

This workspace contains a Django backend and a React frontend. The backend includes a management command to import JSON data from `backend/backend/data/*.json` into Postgres.

Quick start (development):

1. Copy env: `cp backend/.env.example backend/.env`
2. Start services: `docker compose up --build`

The web service runs migrations and executes the import command on each start.

## Twitter/X bookmark trigger

The backend now exposes `POST /api/items/bookmark_fetch/` for browser-side automation. For Twitter's internal bookmark button, use the browser extension in [browser-extension/](browser-extension/) to detect the click and POST the current tweet URL to the local backend. The server then resolves the matching Item, fetches the image candidates, and saves them to the DB.

If your backend is not on `http://localhost:8000`, adjust the extension's backend origin in [browser-extension/background.js](browser-extension/background.js).
