"""Standalone entry point for the PyInstaller-packaged distribution.

Boots the exact same Django app the docker workflow runs, but wired for a
single local user instead of docker-compose's multi-container setup:
SQLite instead of Postgres (see backend.settings' DB_ENGINE toggle), and
waitress (pure-Python, works fine frozen into a PyInstaller build, no
separate process to manage) instead of gunicorn/the poller's own container.

Everything this app persists (the DB, downloaded tagger models, the
locally-trained character classifier, the Twitter/Pixiv creds encryption
keys, the server log) lives under USER_DATA_DIR, NOT wherever PyInstaller
happens to extract the bundle to — a `--onefile` build unpacks into a
temp directory that is deleted after the process exits, so anything
written there would vanish on every relaunch. Only the frontend's static
build (read-only, identical every launch) is served straight out of the
bundle itself.

The app window itself is a native pywebview window (not a browser tab) —
closing it ends the process. The build is windowed (console=False in
fanart_viewer.spec), so there's no console to see output in; everything
that would have printed to it goes to server.log under USER_DATA_DIR
instead (set up first, below, before anything else can print or log).
"""
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

# --- Persistent per-user data directory -------------------------------
# ~/.fanart_viewer on every OS (Path.home() resolves to %USERPROFILE% on
# Windows) — deliberately NOT inside the install location, so an app
# update (replacing the exe) never touches the user's actual archive.
USER_DATA_DIR = Path.home() / '.fanart_viewer'
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# windowed (console=False) builds have no console at all on Windows —
# sys.stdout/stderr can even be None there, so this has to happen before
# any print()/logging call, including ones further down this file.
_log_file = open(USER_DATA_DIR / 'server.log', 'a', encoding='utf-8', buffering=1)
sys.stdout = _log_file
sys.stderr = _log_file
logging.basicConfig(stream=_log_file, level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

os.environ.setdefault('DB_ENGINE', 'sqlite3')
os.environ.setdefault('SQLITE_PATH', str(USER_DATA_DIR / 'db.sqlite3'))
# tagger.py's own _data_dir() already reads this exact env var — covers
# the downloaded base tagger model, the HF cache, AND the locally-trained
# character_classifier_*.joblib (all three live under _data_dir()).
os.environ.setdefault('TAGGER_CACHE_DIR', str(USER_DATA_DIR / 'tagger_cache'))
# Playwright's own Chromium download target — item/playwright_setup.py
# downloads Chromium here on first actual use (never bundled into the
# exe itself; only the Playwright package + its driver are). Redirecting
# this away from Playwright's OS-default cache dir means the download
# survives app updates and lives alongside this app's other data.
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(USER_DATA_DIR / 'playwright_browsers'))
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


def _persistent_fernet_key(filename):
    """Same pattern as the Django secret key above, for item.twitter_creds
    / item.pixiv_creds's own encryption keys — generated once, reused
    across launches, so credentials saved through the settings panel
    stay decryptable after a restart."""
    from cryptography.fernet import Fernet

    path = USER_DATA_DIR / filename
    if not path.exists():
        path.write_text(Fernet.generate_key().decode())
    return path.read_text().strip()


os.environ.setdefault('TWITTER_CREDS_ENC_KEY', _persistent_fernet_key('twitter_creds_key.txt'))
os.environ.setdefault('PIXIV_CREDS_ENC_KEY', _persistent_fernet_key('pixiv_creds_key.txt'))


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


def _serve_forever():
    serve(application, host=HOST, port=PORT)


def _poller_loop():
    """In-process replacement for docker-compose's separate `poller`
    container (poller_entrypoint.sh -> manage.py poll_twitter_updates).
    There's no second process to run here, so this just calls the same
    management command's single-tick mode (`--once`, already exercised
    for manual testing) on a timer instead. poll_twitter_updates._tick()
    itself already no-ops quickly when no Twitter credentials are set, so
    it's safe to always run this rather than gating it on setup state.
    """
    tick_seconds = int(os.environ.get('POLLER_TICK_SECONDS', '360'))
    while True:
        try:
            call_command('poll_twitter_updates', once=True)
        except Exception:
            logging.getLogger(__name__).exception('poller tick failed')
        time.sleep(tick_seconds)


if __name__ == '__main__':
    threading.Thread(target=_poller_loop, daemon=True).start()
    threading.Thread(target=_serve_forever, daemon=True).start()
    logging.getLogger(__name__).info('fanart_viewer starting at http://%s:%s/ (data: %s)', HOST, PORT, USER_DATA_DIR)

    import webview  # noqa: E402

    time.sleep(1.0)  # give waitress a moment to bind before pointing the window at it
    webview.create_window('fanart_viewer', f'http://{HOST}:{PORT}/', width=1280, height=860)
    webview.start()
    # webview.start() blocks until the window is closed; both background
    # threads above are daemons, so returning here ends the process — no
    # separate shutdown step needed.
