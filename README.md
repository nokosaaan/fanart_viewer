# fanart_viewer

This workspace contains a Django backend and a React frontend. The backend includes a management command to import JSON data from `backend/backend/data/*.json` into Postgres.

Quick start (development):

1. Copy env: `cp backend/.env.example backend/.env`
2. Start services: `docker compose up --build`

The web service runs migrations and executes the import command on each start.
