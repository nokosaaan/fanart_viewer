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

## Windows standalone build (exe)

This packages the app as a single `fanart_viewer.exe` — SQLite instead of
Postgres, no docker, no separate poller container. Each user gets their
own local DB and settings under `%USERPROFILE%\.fanart_viewer`; nothing
here is bundled into the exe itself. Build scripts live in [exe/](exe/)
(`build.ps1` for Windows, `build.sh` for Linux/WSL — the latter only
produces a Linux binary, useful for testing the packaging itself, not a
real .exe: PyInstaller can't cross-compile).

### 1. Prerequisites

- **Python 3.10 or 3.11** from [python.org](https://www.python.org/downloads/)
  (check "Add python.exe to PATH" during install). Afterwards, confirm
  `where.exe python` points at `...\Programs\Python\Python3XX\python.exe`,
  **not** `...\AppData\Local\Microsoft\WindowsApps\python.exe` — that path
  is a Microsoft Store stub, not a real Python install, and `python
  --version`/`python -m venv` silently do nothing useful under it. If it
  still wins, disable the `python`/`python3` entries under Windows Settings
  → Apps → Advanced app settings → App execution aliases.
- **Node.js LTS** from [nodejs.org](https://nodejs.org/) (the plain
  Windows installer `.msi`, not the Docker/nvm/etc. options also listed
  there) — needed to build the frontend. Confirm with `node --version`.
- If PowerShell refuses to run any `.ps1` script at all
  (`...スクリプトの実行が無効になっている...`), run it as:
  `powershell -ExecutionPolicy Bypass -File .\build.ps1`, or once per
  session: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

Open a **new** PowerShell window after installing either of the above so
PATH changes take effect.

### 2. One-time environment setup

```powershell
git clone <this repo>
cd fanart_viewer
git checkout exe-packaging
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r backend\requirements.txt
pip install -r exe\requirements.txt
# Person/head-box detection (used by the region-labeling queue's "自動検出"
# button) needs this too — --no-deps is required, not optional: some of
# its declared dependencies don't have Windows-compatible wheels, but
# nothing it actually imports from item.tagger needs them.
pip install --no-deps dghs-imgutils pyrfc6266

cd frontend
npm install
npm run build
cd ..
```

### 3. Build

```powershell
cd exe
.\build.ps1
```

Produces `exe\dist\fanart_viewer.exe`.

### 4. Rebuilding after pulling new code

The exe is a compiled snapshot — `git pull` alone changes nothing already
running or already built. To pick up new code: `git pull`, then re-run
`npm run build` in `frontend\` (only if frontend files changed) and
`.\build.ps1` in `exe\` again. Settings/data stored in the database
(credentials, poller settings, etc.) do *not* need a rebuild — those take
effect immediately, on next launch or even without one.

### 5. Running it

Double-click `fanart_viewer.exe`, or run it from PowerShell (recommended
the first few times — see below). A window opens with a loading spinner,
then switches to the app once the local server responds. Closing the
window stops the app entirely (no separate process left running).

There is no console window (a windowed/GUI build, not a console app) — if
something goes wrong before the window would normally appear, check
`%USERPROFILE%\.fanart_viewer\server.log` for the traceback.

First-run setup, all from the header menu inside the app:
- **Twitter/X 認証情報** — paste `auth_token`/`ct0` (and optionally `twid`)
  from a logged-in x.com session's cookies. Same panel also has the
  background-poller opt-in (off by default) and its rate (件数 / 分・時間・
  日・週ごと) — this single setting covers both the Twitter and Pixiv
  pollers.
- **Pixiv 認証情報** — paste `PHPSESSID` from a logged-in pixiv.net
  session's cookies (preferred over username/password, which can fail on
  CAPTCHA/2FA).
- **バックアップ** — Google Drive backup/restore. Needs a Google Cloud
  OAuth client (Console → APIs & Services → Credentials → Create OAuth
  client ID → Desktop app), then its Client ID/Secret pasted into the
  "認証する" form — this opens your system browser for Google's consent
  screen and stores the resulting token automatically (no .env editing).

## Twitter/X bookmark trigger

The backend exposes `POST /api/items/bookmark_fetch/` for browser-side automation. Use the browser extension in [browser-extension/](browser-extension/) to detect the click and POST the current tweet URL to the local backend. The server resolves the matching Item, fetches the image candidates, and saves them to the DB.

If your backend is not on `http://localhost:8000`, adjust the extension's backend origin in [browser-extension/background.js](browser-extension/background.js).
