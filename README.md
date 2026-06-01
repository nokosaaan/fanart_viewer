# tutorial

See this [local release guide](RELEASE_LOCAL.md).

# fanart_viewer

This repository contains a Django backend and a React frontend. The local setup is Docker Compose based.

Quick start:

1. Copy env files:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

2. Start services:

```bash
docker compose up --build
```

The web service runs migrations on startup.

## Twitter/X bookmark trigger

The backend exposes `POST /api/items/bookmark_fetch/` for browser-side automation. Use the browser extension in [browser-extension/](browser-extension/) to detect the click and POST the current tweet URL to the local backend. The server resolves the matching Item, fetches the image candidates, and saves them to the DB.

If your backend is not on `http://localhost:8000`, adjust the extension's backend origin in [browser-extension/background.js](browser-extension/background.js).
