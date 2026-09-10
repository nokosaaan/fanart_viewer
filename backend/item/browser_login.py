"""Captures Twitter/Pixiv/Poipiku login session cookies from a real,
visible Chromium window instead of asking the user to open DevTools and
copy cookie values by hand — mirrors the exact "open a real browser, let
the user finish logging in normally, capture the result programmatically"
shape drive_creds_authenticate_view already uses for Google's OAuth
consent screen (InstalledAppFlow.run_local_server). Those three sites have
no OAuth redirect to hook into (a plain cookie-session login, not a token
exchange), so instead of a local HTTP callback server, the launched
browser is kept open across two separate requests: `open_login` opens the
real login page, and `capture_and_apply` — triggered by the user clicking
"ログイン完了" in THIS app's own panel once they've finished logging in in
the OTHER, real browser window — reads back whatever cookies actually got
set, saves them via the matching *_creds module, and closes the browser.

All actual Playwright calls happen on ONE dedicated worker thread (started
lazily on first use, kept alive for the app's whole lifetime) rather than
whichever request thread happens to handle a given view — waitress (see
exe/launcher.py) serves requests from a thread pool, and Playwright's sync
API is only safe to drive from the single thread that created its
objects. Views/callers submit small commands to the worker over a queue
and block (with a timeout) for the reply instead of touching Playwright
objects directly.
"""
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

_LOGIN_URLS = {
    'twitter': 'https://x.com/login',
    'pixiv': 'https://accounts.pixiv.net/login',
    # No dedicated login URL — poipiku's is a modal reached from the
    # homepage; navigating there and letting the user find it themselves
    # is fine, since all this needs afterward is whatever cookies end up
    # set on the poipiku.com domain, not any particular page.
    'poipiku': 'https://poipiku.com/',
}

# Which cookie NAMES to pull off the launched context after login, and
# which substring its domain must contain — a bare name like JSESSIONID
# is common enough across unrelated sites that domain-filtering matters.
_COOKIE_SPECS = {
    'twitter': {'domain_contains': ('x.com', 'twitter.com'), 'names': ('auth_token', 'ct0', 'twid')},
    'pixiv': {'domain_contains': ('pixiv.net',), 'names': ('PHPSESSID',)},
    'poipiku': {'domain_contains': ('poipiku.com',), 'names': ('POIPIKU_LK', 'JSESSIONID')},
}

_cmd_q = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False

_state_lock = threading.Lock()
_state = {'platform': None, 'started_at': None}


def get_status() -> dict:
    """Whether a login window is currently open, and for which platform —
    so the frontend can render "ログイン完了を待っています" correctly even
    after the panel is closed and reopened, instead of losing track of an
    already-open window."""
    with _state_lock:
        return dict(_state)


def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        _worker_started = True


def _worker_loop():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = None
    context = None

    def _close():
        nonlocal browser, context
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        browser, context = None, None
        with _state_lock:
            _state['platform'] = None
            _state['started_at'] = None

    while True:
        cmd, payload, reply_q = _cmd_q.get()
        try:
            if cmd == 'open':
                platform = payload
                if browser is not None:
                    reply_q.put((False, 'already_open'))
                    continue
                browser = pw.chromium.launch(
                    headless=False, args=['--no-sandbox', '--disable-dev-shm-usage'],
                )
                context = browser.new_context()
                page = context.new_page()
                page.goto(_LOGIN_URLS[platform], timeout=30000)
                with _state_lock:
                    _state['platform'] = platform
                    _state['started_at'] = time.time()
                reply_q.put((True, None))

            elif cmd == 'capture':
                if context is None:
                    reply_q.put((False, 'not_open'))
                    continue
                cookies = context.cookies()
                reply_q.put((True, cookies))
                _close()

            elif cmd == 'cancel':
                _close()
                reply_q.put((True, None))

            elif cmd == 'shutdown':
                _close()
                reply_q.put((True, None))
                break
        except Exception as e:
            logger.exception('browser_login worker command %r failed', cmd)
            reply_q.put((False, str(e)))
            _close()

    try:
        pw.stop()
    except Exception:
        pass


def _call(cmd, payload=None, timeout=40):
    _ensure_worker()
    reply_q = queue.Queue()
    _cmd_q.put((cmd, payload, reply_q))
    try:
        ok, result = reply_q.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError('操作がタイムアウトしました')
    if not ok:
        raise RuntimeError(result or '不明なエラー')
    return result


def open_login(platform: str):
    """Opens a real, visible browser window at `platform`'s own login
    page. The window stays open (tracked by the worker thread) until
    capture_and_apply or cancel_login is called for it."""
    if platform not in _LOGIN_URLS:
        raise ValueError(f'unknown platform {platform!r}')
    from .playwright_setup import ensure_chromium_installed
    ensure_chromium_installed()
    _call('open', platform, timeout=60)


def capture_and_apply(platform: str) -> dict:
    """Reads cookies from the still-open browser, extracts this
    platform's ones, saves them via the matching *_creds.set_credentials,
    and closes the browser. Raises RuntimeError (never silently stores an
    empty/partial credential set) if nothing usable was found — most
    likely the user closed the window, or clicked "ログイン完了" before
    actually finishing the login form.
    """
    if platform not in _COOKIE_SPECS:
        raise ValueError(f'unknown platform {platform!r}')
    cookies = _call('capture', platform, timeout=15)

    spec = _COOKIE_SPECS[platform]
    found = {}
    for c in cookies:
        domain = (c.get('domain') or '').lstrip('.')
        if not any(hint in domain for hint in spec['domain_contains']):
            continue
        name = c.get('name')
        if name in spec['names']:
            found[name] = c.get('value')

    if platform == 'twitter':
        if not found.get('auth_token') or not found.get('ct0'):
            raise RuntimeError(
                'ログインが完了していないようです(auth_token/ct0が見つかりません)。'
                'ログインを完了してからもう一度お試しください。'
            )
        from . import twitter_creds
        twitter_creds.set_credentials(found['auth_token'], found['ct0'], twid=found.get('twid'))
        return twitter_creds.status()

    if platform == 'pixiv':
        if not found.get('PHPSESSID'):
            raise RuntimeError(
                'ログインが完了していないようです(PHPSESSIDが見つかりません)。'
                'ログインを完了してからもう一度お試しください。'
            )
        from . import pixiv_creds
        pixiv_creds.set_credentials(phpsessid=found['PHPSESSID'])
        return pixiv_creds.status()

    if platform == 'poipiku':
        if not found.get('POIPIKU_LK') and not found.get('JSESSIONID'):
            raise RuntimeError(
                'ログインが完了していないようです(必要なCookieが見つかりません)。'
                'ログインを完了してからもう一度お試しください。'
            )
        from . import poipiku_creds
        poipiku_creds.set_credentials(lk=found.get('POIPIKU_LK'), jsessionid=found.get('JSESSIONID'))
        return poipiku_creds.status()


def cancel_login(platform: str):
    """Closes the login window without capturing anything — used when the
    user gives up, or opened the wrong platform's window."""
    _call('cancel', platform, timeout=15)
