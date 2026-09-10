"""
Pixiv bookmarks fetcher using pixiv.net's own internal ajax API
(/ajax/user/{uid}/illusts/bookmarks), mirroring item.twitter_gql_fetch's
approach but considerably simpler: Pixiv's bookmarks endpoint is directly
offset-paginated (a plain `offset` query param), unlike Twitter's opaque
pagination cursor, and there's no separate "Likes" concept to track
alongside it.

Required: a PHPSESSID cookie from a logged-in pixiv.net session (see
item.pixiv_creds -- the same credential store playwright_helper.py's
Pixiv fetcher already uses).

Requests go through a real (headless) Chromium browser context via
Playwright's ctx.request.get() -- the same pattern playwright_helper.py's
own Pixiv fetcher already uses for its ajax calls -- rather than the
`requests` library directly. This was a deliberate fix, not the original
design: a plain `requests.get('https://www.pixiv.net/', cookies=...)`
was tried first and failed live with `requests.exceptions.TooManyRedirects:
Exceeded 30 redirects`, repeatedly, against a real account -- consistent
with Pixiv (or a CDN/WAF in front of it) redirect-looping traffic that
doesn't look like a real browser. ctx.request.get shares the real
Chromium network stack/TLS fingerprint with the browser context it's
created from, which playwright_helper.py's own already-working Pixiv
calls rely on for the same reason.

NOTE: the bookmarks endpoint's exact response shape (particularly
`body.works[].id`) is based on Pixiv's known ajax API conventions (the
same family of endpoints playwright_helper.py's _master/img-original URL
reconstruction already relies on) but has not been exercised against a
live, authenticated session in development -- verify against a real
response (DevTools -> Network tab while opening your own bookmarks page)
if bookmarks stop showing up as expected. gallery-dl (already a
dependency of this project) was checked as a possible reference for
this, but its Pixiv support goes through the separate mobile-app OAuth
API instead of PHPSESSID-based ajax calls, so it wasn't directly
reusable here.

Resolving "my own user id" specifically (resolve_own_user_id, below) used
to rely on a `<meta id="meta-global-data">` tag pixiv.net embedded on
every page -- confirmed LIVE (2026-09-10, a real saved bookmarks page
from an actual account) that this tag no longer exists anywhere in the
page at all (pixiv's frontend moved to a server-rendered Next.js page).
The replacement source is the page's own `<script id="__NEXT_DATA__">`
tag: a JSON blob whose `props.pageProps.serverSerializedPreloadedState`
field is itself a JSON-encoded STRING (double-encoded -- the whole
Redux/preloaded-state tree serialized once, then embedded as a string
value inside the outer JSON) containing `userData.self.id` -- verified
against that same real saved page (self.id there matched the account's
own numeric id from the page's URL).
"""
import contextlib
import json
import logging
import re

logger = logging.getLogger(__name__)

_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
)
_PAGE_SIZE = 48
_NEXT_DATA_RE = re.compile(
    r'<script\s+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
)


def _find_next_data_json(html_text: str) -> str | None:
    match = _NEXT_DATA_RE.search(html_text)
    return match.group(1) if match else None


class PixivAuthError(RuntimeError):
    """Raised when Pixiv rejects the session (expired/invalid PHPSESSID)."""


class PixivAPIError(RuntimeError):
    """Raised for any other unexpected API/HTTP failure."""


def _get_phpsessid() -> str:
    from .pixiv_creds import get_credentials

    return get_credentials()['phpsessid']


@contextlib.contextmanager
def _pixiv_request_context():
    """A Playwright browser context with the stored PHPSESSID cookie
    injected, for making ctx.request.get() calls through -- see this
    module's own docstring for why this replaced plain `requests` calls.
    Downloads Chromium on first use if it isn't already present (see
    item.playwright_setup); never bundled into the exe itself.
    """
    phpsessid = _get_phpsessid()
    if not phpsessid:
        raise RuntimeError('Pixiv認証情報が設定されていません(ヘッダーメニューの「Pixiv 認証情報」から設定してください)')

    from playwright.sync_api import sync_playwright

    from .playwright_setup import ensure_chromium_installed

    ensure_chromium_installed()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{
                'name': 'PHPSESSID', 'value': phpsessid, 'domain': '.pixiv.net', 'path': '/',
            }])
            yield ctx
        finally:
            browser.close()


def resolve_own_user_id() -> str:
    """The logged-in account's numeric user id -- needed since the
    bookmarks endpoint is per-user, not "my own" implicitly. See this
    module's docstring for where this comes from (the page's own
    __NEXT_DATA__ script tag, not a dedicated API call).

    Raises:
        PixivAuthError: not logged in / session invalid
        PixivAPIError:  page didn't load, or its expected structure wasn't found
        RuntimeError:   PHPSESSID not configured
    """
    with _pixiv_request_context() as ctx:
        resp = ctx.request.get(
            'https://www.pixiv.net/',
            headers={'User-Agent': _UA, 'Accept': 'text/html'},
            timeout=20000,
        )
        if resp.status != 200:
            raise PixivAPIError(f'HTTP {resp.status}')
        body_text = resp.text()

    raw_next_data = _find_next_data_json(body_text)
    if raw_next_data is None:
        logger.warning('pixiv_bookmarks_fetch: __NEXT_DATA__ script tag not found in response')
        raise PixivAPIError('ページ内にユーザー情報が見つかりませんでした(ページ構造が変わった可能性)')

    try:
        next_data = json.loads(raw_next_data)
    except ValueError as e:
        raise PixivAPIError(f'ページ情報の解析に失敗しました: {e}') from e

    preloaded_raw = (next_data.get('props') or {}).get('pageProps', {}).get('serverSerializedPreloadedState')
    if not preloaded_raw:
        logger.warning('pixiv_bookmarks_fetch: serverSerializedPreloadedState not found in __NEXT_DATA__')
        raise PixivAPIError('ページ内にユーザー情報が見つかりませんでした(ページ構造が変わった可能性)')

    try:
        preloaded = json.loads(preloaded_raw)
    except ValueError as e:
        raise PixivAPIError(f'ユーザー情報の解析に失敗しました: {e}') from e

    self_data = (preloaded.get('userData') or {}).get('self') or {}
    if not self_data.get('id'):
        raise PixivAuthError('Pixivセッションが無効です(PHPSESSIDの期限切れの可能性)')

    return str(self_data['id'])


def fetch_account_bookmarks(
    user_id: str, known_ids: set, max_pages: int = 1, start_offset: int = 0,
) -> tuple[list[dict], int]:
    """Scan the given user's own bookmarks newest-first, returning
    (candidates, next_offset). candidates are the ones not already in
    known_ids, still newest-first order. Stops as soon as an already-known
    illust is seen (same early-stop logic as Twitter's Bookmarks scan) --
    costing just one page per tick once caught up.

    next_offset resumes a still-catching-up scan across ticks (mirrors
    TwitterPollState's resume cursor -- see that module's own docstring):
    0 once caught up (nothing to resume), otherwise the offset to
    continue from next tick.

    Raises:
        PixivAuthError: session invalid/expired
        PixivAPIError:  unexpected HTTP/API failure
        RuntimeError:   credentials not configured
    """
    candidates = []
    offset = start_offset
    with _pixiv_request_context() as ctx:
        for _ in range(max_pages):
            resp = ctx.request.get(
                f'https://www.pixiv.net/ajax/user/{user_id}/illusts/bookmarks',
                params={'tag': '', 'offset': offset, 'limit': _PAGE_SIZE, 'rest': 'show'},
                headers={
                    'User-Agent': _UA, 'Accept': 'application/json',
                    'Referer': f'https://www.pixiv.net/users/{user_id}/bookmarks/artworks',
                },
                timeout=20000,
            )
            if resp.status == 401:
                raise PixivAuthError('Pixivセッションが無効です(PHPSESSIDの期限切れの可能性)')
            if resp.status != 200:
                raise PixivAPIError(f'HTTP {resp.status}')

            data = resp.json()
            if data.get('error'):
                raise PixivAPIError(data.get('message') or 'unknown error')

            works = (data.get('body') or {}).get('works') or []
            if not works:
                return candidates, 0  # nothing more to backfill

            caught_up = False
            for w in works:
                illust_id = int(w['id'])
                if illust_id in known_ids:
                    caught_up = True
                    break
                candidates.append({
                    'illust_id': illust_id,
                    'user_name': w.get('userName') or '',
                    'description': w.get('title') or '',
                })

            offset += len(works)
            if caught_up:
                return candidates, 0

    return candidates, offset
