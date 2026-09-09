"""Standalone entry point for the PyInstaller-packaged distribution.

Boots the exact same Django app the docker workflow runs, but wired for a
single local user instead of docker-compose's multi-container setup:
SQLite instead of Postgres (see backend.settings' DB_ENGINE toggle), and
waitress (pure-Python, works fine frozen into a PyInstaller build, no
separate process to manage) instead of gunicorn/the poller's own container.

Everything this app persists (the DB, downloaded tagger models, the
locally-trained character classifier, the Twitter creds encryption key)
lives under USER_DATA_DIR, NOT wherever PyInstaller happens to extract the
bundle to — a `--onefile` build unpacks into a temp directory that is
deleted after the process exits, so anything written there would vanish
on every relaunch. Only the frontend's static build (read-only, identical
every launch) is served straight out of the bundle itself.
"""
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

# --- Persistent per-user data directory -------------------------------
# ~/.fanart_viewer on every OS (Path.home() resolves to %USERPROFILE% on
# Windows) — deliberately NOT inside the install location, so an app
# update (replacing the exe) never touches the user's actual archive.
USER_DATA_DIR = Path.home() / '.fanart_viewer'
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault('DB_ENGINE', 'sqlite3')
os.environ.setdefault('SQLITE_PATH', str(USER_DATA_DIR / 'db.sqlite3'))
# tagger.py's own _data_dir() already reads this exact env var — covers
# the downloaded base tagger model, the HF cache, AND the locally-trained
# character_classifier_*.joblib (all three live under _data_dir()).
os.environ.setdefault('TAGGER_CACHE_DIR', str(USER_DATA_DIR / 'tagger_cache'))
os.environ.setdefault('DJANGO_DEBUG', '0')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
os.environ.setdefault('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')

# A real random secret key, generated once and reused across launches
# (settings.py refuses to start in non-DEBUG mode with the placeholder
# default) — regenerating it every launch would silently invalidate every
# existing session cookie/CSRF token each time the app restarts.
_secret_key_path = USER_DATA_DIR / 'secret_key.txt'
if not _secret_key_path.exists():
    _secret_key_path.write_text(secrets.token_hex(32))
os.environ.setdefault('DJANGO_SECRET_KEY', _secret_key_path.read_text().strip())


def _bundle_path(relative):
    """Bundled, read-only assets — these DO live inside the PyInstaller
    extraction dir, since they're static per-build (rebuilt fresh every
    release) and the app never writes to them at runtime."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


os.environ.setdefault('FRONTEND_DIST', _bundle_path('frontend_dist'))

# Make the bundled `backend/` package importable — PyInstaller's onefile
# build extracts everything under sys._MEIPASS, and this script lives
# alongside `backend/` there (see build.spec's Analysis() pathex/datas).
sys.path.insert(0, _bundle_path('.'))

# NOTE: modules Django only ever loads dynamically by name (from string
# settings: DJANGO_SETTINGS_MODULE, ROOT_URLCONF, INSTALLED_APPS,
# MIDDLEWARE, REST_FRAMEWORK's various DEFAULT_*_CLASSES) are invisible to
# PyInstaller's static import analysis, so without help each one is
# silently left out of the frozen build and django.setup() fails with
# ModuleNotFoundError despite the build itself completing without error.
# Deliberately NOT force-imported here with a plain `import` statement —
# item.models (transitively pulled in by item.urls/item.views) defines
# actual model classes, which requires django.setup() to have already run
# (AppRegistryNotReady otherwise), so any such import must happen AFTER
# setup(), not before. Instead, every one of these is listed as a
# --hidden-import on the PyInstaller command line (see build.sh) — that
# only affects what gets bundled, not when this script executes anything,
# so django.setup() below still imports each of them itself, in Django's
# own correct order.

import django  # noqa: E402  (must follow the env var setup above)

django.setup()

from django.core.management import call_command  # noqa: E402

# Idempotent — safe to run on every launch, not just the first. This is
# the packaged build's replacement for `docker compose exec web python
# manage.py migrate`, which obviously isn't available here.
call_command('migrate', interactive=False)

from waitress import serve  # noqa: E402

from backend.wsgi import application  # noqa: E402

HOST = '127.0.0.1'
PORT = 8000


def _open_browser_when_ready():
    time.sleep(1.5)
    webbrowser.open(f'http://{HOST}:{PORT}/')


if __name__ == '__main__':
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    print(f'fanart_viewer starting at http://{HOST}:{PORT}/ (data: {USER_DATA_DIR})')
    serve(application, host=HOST, port=PORT)
