"""Standalone entry point for the PyInstaller-packaged distribution.

Boots the exact same Django app the docker workflow runs, but wired for a
single local user instead of docker-compose's multi-container setup:
SQLite instead of Postgres (see backend.settings' DB_ENGINE toggle), and
waitress (pure-Python, works fine frozen into a PyInstaller build, no
separate process to manage) instead of gunicorn/the poller's own container.

Everything this app persists (the DB, downloaded tagger models, the
locally-trained character classifier, the Twitter/Pixiv creds encryption
keys, the server log) lives under USER_DATA_DIR, NOT wherever the frozen
build's own files live (fanart_viewer.spec builds a `COLLECT()`/onedir
distribution, not `--onefile` — a folder next to the exe, not a temp
extraction dir — but nothing here should assume otherwise, since the
same code works with either). Only the frontend's static build (read-
only, identical every launch) is served straight out of the bundle itself.

The app window itself is a native pywebview window (not a browser tab) —
closing it ends the process. The build is windowed (console=False in
fanart_viewer.spec), so there's no console to see output in; everything
that would have printed to it goes to server.log under USER_DATA_DIR
instead (set up first, below, before anything else can print or log).

Startup order: fanart_viewer.spec's Splash screen (a lightweight Tk
window the bootloader itself shows, PyInstaller's own feature) covers
the gap before this script has even started running -- this module's
own imports, then django.setup() and friends, still take a moment, so
pyi_splash.update_text() below keeps it showing real progress instead of
a static image. Once that splash closes, the pywebview window (already
created, showing _LOADING_HTML's spinner) takes over, and
django.setup()/migrate/etc. run on a background thread (Django itself,
plus onnxruntime and friends pulled in transitively, take a few real
seconds to import — showing nothing during that stretch reads as a
hang). webview.start(func, args) is pywebview's own supported way to run
exactly this kind of background init after the window appears.
"""
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

try:
    import pyi_splash  # only importable inside a PyInstaller-frozen build
except ImportError:
    pyi_splash = None

# --train-classifier lets exe/train.ps1 (Windows) / train.sh (Linux) run
# item.management.commands.train_character_classifier against this same
# frozen build, with no separate Python/venv needed — the exe ships with
# NO pretrained character classifier at all (a blank "canary" state; see
# that command's own docstring), so this is how a user actually grows one
# from their own DB, on their own schedule. Skips the webview/server/
# poller entirely; any args after the flag are forwarded verbatim as this
# command's own CLI options (e.g. --min-images, --include-multi-character).
TRAIN_FLAG = '--train-classifier'
IS_TRAINING_MODE = TRAIN_FLAG in sys.argv

# --run-gallery-dl: item.gallerydl_fetch normally shells out to a
# `gallery-dl` executable that pip's install creates on PATH -- which
# doesn't exist anywhere in a frozen build (only gallery_dl the PYTHON
# PACKAGE gets bundled, no separate wrapper binary). Detected there via
# sys.frozen and re-invoked as `[sys.executable, '--run-gallery-dl', *args]`
# instead -- this same exe, in a fresh child process (for the same
# isolation a real separate gallery-dl process would have; gallery_dl.
# main() itself mutates global config/logging state, not safe to call
# concurrently in-process). Everything after the flag is passed straight
# through as gallery-dl's own argv, and stdout/stderr are deliberately
# left untouched (no log-file redirect below) since the parent process's
# subprocess.run(capture_output=True) needs to see them directly, exactly
# like it would from a real standalone gallery-dl.
RUN_GALLERYDL_FLAG = '--run-gallery-dl'
IS_GALLERYDL_MODE = RUN_GALLERYDL_FLAG in sys.argv

if pyi_splash:
    if IS_TRAINING_MODE or IS_GALLERYDL_MODE:
        pyi_splash.close()
    else:
        pyi_splash.update_text('起動準備中…')

if IS_GALLERYDL_MODE:
    _flag_index = sys.argv.index(RUN_GALLERYDL_FLAG)
    _gallerydl_argv = sys.argv[_flag_index + 1:]
    import gallery_dl

    sys.argv = ['gallery-dl', *_gallerydl_argv]
    sys.exit(gallery_dl.main())

# --- Persistent per-user data directory -------------------------------
# ~/.fanart_viewer on every OS (Path.home() resolves to %USERPROFILE% on
# Windows) — deliberately NOT inside the install location, so an app
# update (replacing the exe) never touches the user's actual archive.
USER_DATA_DIR = Path.home() / '.fanart_viewer'
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# windowed (console=False) builds have no console at all on Windows —
# sys.stdout/stderr can even be None there, so this has to happen before
# any print()/logging call, including ones further down this file. Training
# mode gets its own log file so a training run's (often long, verbose)
# output doesn't interleave with the running app's server.log.
_log_filename = 'training.log' if IS_TRAINING_MODE else 'server.log'
_log_file = open(USER_DATA_DIR / _log_filename, 'a', encoding='utf-8', buffering=1)
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
# Gates force_method='playwright' in item/views.py -- an explicit opt-in
# for the docker deployment (where headless fetching is a meaningful
# resource/security tradeoff on a shared/exposed server), but the exe is
# a single local user's own machine already running this same code
# unattended, so there's no separate "should this be allowed" question
# to ask here the way there is there. Confirmed live: Playwright/headless
# fetch requests were silently 403ing before this was added.
os.environ.setdefault('HEADLESS_ALLOWED', '1')

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
os.environ.setdefault('DRIVE_CREDS_ENC_KEY', _persistent_fernet_key('drive_creds_key.txt'))
os.environ.setdefault('POIPIKU_CREDS_ENC_KEY', _persistent_fernet_key('poipiku_creds_key.txt'))


def _bundle_path(relative):
    """Bundled, read-only assets — these DO live inside the PyInstaller
    extraction dir, since they're static per-build (rebuilt fresh every
    release) and the app never writes to them at runtime."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


os.environ.setdefault('FRONTEND_DIST', _bundle_path('frontend_dist'))

# Make the bundled `backend/` package importable — sys._MEIPASS points at
# wherever this build's own files actually live (the onedir folder here;
# a temp extraction dir for a `--onefile` build), and this script lives
# alongside `backend/` there (see fanart_viewer.spec's Analysis() datas).
sys.path.insert(0, _bundle_path('.'))

HOST = '127.0.0.1'
PORT = 8000

_LOADING_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif; }
  .wrap { display:flex; flex-direction:column; align-items:center; }
  .spinner { width:36px; height:36px; border:4px solid #334155; border-top-color:#3b82f6;
             border-radius:50%; animation:spin 0.8s linear infinite; margin-bottom:16px; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style></head>
<body><div class="wrap"><div class="spinner"></div><div id="status">起動中…</div></div></body></html>
"""


def _set_status(window, text):
    """Updates _LOADING_HTML's status line via JS injection -- the
    pyi_splash-based splash screen (see fanart_viewer.spec) only covers
    the gap before this window exists at all; from here on, this is the
    one place progress is shown, so every step worth waiting on updates
    it (django.setup, migrate, starting the server)."""
    try:
        window.evaluate_js(f"document.getElementById('status').innerText = {text!r}")
    except Exception:
        pass


def _poller_loop():
    """In-process replacement for docker-compose's separate `poller`
    container (poller_entrypoint.sh -> manage.py poll_twitter_updates).
    There's no second process to run here, so this just calls the same
    management commands' single-tick mode (`--once`, already exercised
    for manual testing) on a timer instead -- both Twitter's and Pixiv's
    poller share one PollerSettings row/interval, so they tick together.
    Each command's own _tick() already no-ops quickly when disabled/no
    credentials are set, so it's safe to always call both rather than
    duplicating that check here — but the sleep interval between ticks is
    this loop's own responsibility, and is re-read from PollerSettings
    every cycle (not cached at thread start) so a change made through the
    settings panel takes effect after the current tick, no restart needed.
    """
    from django.core.management import call_command
    from item.models import PollerSettings

    while True:
        for command in ('poll_twitter_updates', 'poll_pixiv_bookmarks'):
            try:
                call_command(command, once=True)
            except Exception:
                logging.getLogger(__name__).exception('%s tick failed', command)
        try:
            interval = PollerSettings.objects.get(pk=1).interval_seconds
        except PollerSettings.DoesNotExist:
            interval = 360
        time.sleep(interval)


def _start_backend(window):
    """Runs on a background thread — see webview.start() below — so the
    loading window (already showing) doesn't have to wait for Django and
    its heavier transitive imports (onnxruntime etc.), migrate, and the
    servers to start. Swaps the window over to the real app once waitress
    actually answers a request.

    NOTE: modules Django only ever loads dynamically by name (from string
    settings: DJANGO_SETTINGS_MODULE, ROOT_URLCONF, INSTALLED_APPS,
    MIDDLEWARE, REST_FRAMEWORK's various DEFAULT_*_CLASSES) are invisible
    to PyInstaller's static import analysis, so without help each one is
    silently left out of the frozen build and django.setup() fails with
    ModuleNotFoundError despite the build itself completing without
    error. Deliberately NOT force-imported here with a plain `import`
    statement — item.models (transitively pulled in by item.urls/
    item.views) defines actual model classes, which requires
    django.setup() to have already run (AppRegistryNotReady otherwise),
    so any such import must happen AFTER setup(), not before. Instead,
    every one of these is listed as a --hidden-import in the PyInstaller
    spec (see fanart_viewer.spec) — that only affects what gets bundled,
    not when this script executes anything, so django.setup() below
    still imports each of them itself, in Django's own correct order.
    """
    # The pywebview window (with its own _LOADING_HTML spinner) is already
    # showing by the time this runs (webview.start() shows it before
    # spawning this function's thread) -- close the bootloader splash now
    # rather than at the very end, so the two loading indicators don't
    # stack on top of each other for this whole function's duration.
    if pyi_splash:
        pyi_splash.close()
    _set_status(window, 'アプリを初期化中…')

    import django

    django.setup()

    from django.core.management import call_command

    _set_status(window, 'データベースを準備中…')

    # Idempotent — safe to run on every launch, not just the first. This
    # is the packaged build's replacement for `docker compose exec web
    # python manage.py migrate`, which obviously isn't available here.
    call_command('migrate', interactive=False)

    # Unattended background fetching without having asked first isn't
    # something a personal, per-user install should just start doing on
    # its own — unlike PollerSettings' own field-level default (True,
    # which matches the existing always-on docker `poller` service's
    # behavior for anyone already relying on it), this only ever creates
    # the row disabled, and only if it doesn't already exist yet (so a
    # choice made through the settings panel on an earlier launch is
    # never overwritten back to disabled).
    from item.models import PollerSettings

    PollerSettings.objects.get_or_create(pk=1, defaults={'enabled': False})

    from waitress import serve

    from backend.wsgi import application

    _set_status(window, 'サーバーを起動中…')

    threading.Thread(target=_poller_loop, daemon=True).start()
    threading.Thread(target=lambda: serve(application, host=HOST, port=PORT), daemon=True).start()

    logging.getLogger(__name__).info(
        'fanart_viewer starting at http://%s:%s/ (data: %s)', HOST, PORT, USER_DATA_DIR
    )

    import urllib.request

    # Any HTTP response (even a 404/500) proves waitress is accepting
    # connections, which is all this needs to know -- urlopen() raises
    # HTTPError for those, so they must be caught separately from a
    # genuine "not listening yet" connection failure (URLError), or
    # every retry here would burn its full timeout for no reason.
    import urllib.error

    url = f'http://{HOST}:{PORT}/'
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.5)

    window.load_url(url)


def _run_training_mode():
    """No webview/server/poller at all -- just runs the management
    command to completion (or failure) and exits. See TRAIN_FLAG's
    comment above for why this exists."""
    import django

    django.setup()

    from django.core.management import call_command, execute_from_command_line

    call_command('migrate', interactive=False)

    extra_args = [a for a in sys.argv[1:] if a != TRAIN_FLAG]
    logging.getLogger(__name__).info('Starting train_character_classifier %s', extra_args)
    try:
        execute_from_command_line(['fanart_viewer', 'train_character_classifier', *extra_args])
    except SystemExit as e:
        # execute_from_command_line calls sys.exit() itself on completion
        # (success or a handled CommandError) -- let that determine our
        # own exit code instead of masking it.
        raise
    except Exception:
        logging.getLogger(__name__).exception('train_character_classifier crashed')
        sys.exit(1)


if __name__ == '__main__':
    if IS_TRAINING_MODE:
        _run_training_mode()
    else:
        import webview  # noqa: E402

        window = webview.create_window('fanart_viewer', html=_LOADING_HTML, width=1280, height=860)
        webview.start(_start_backend, args=(window,))
        # webview.start() blocks until the window is closed; both background
        # threads started in _start_backend are daemons, so returning here
        # ends the process — no separate shutdown step needed.
