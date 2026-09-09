"""
Pixiv bookmarks fetcher using pixiv.net's own internal ajax API
(/ajax/user/{uid}/illusts/bookmarks) -- browser-free (plain HTTP via
requests), mirroring item.twitter_gql_fetch's approach but considerably
simpler: Pixiv's bookmarks endpoint is directly offset-paginated (a
plain `offset` query param), unlike Twitter's opaque pagination cursor,
and there's no separate "Likes" concept to track alongside it.

Required: a PHPSESSID cookie from a logged-in pixiv.net session (see
item.pixiv_creds -- the same credential store playwright_helper.py's
Pixiv fetcher already uses).

NOTE: this endpoint's exact shape (particularly `body.works[].id`) is
based on Pixiv's known ajax API conventions (the same family of
endpoints playwright_helper.py's _master/img-original URL reconstruction
already relies on) but has not been exercised against a live,
authenticated session in development -- verify against a real response
(DevTools -> Network tab while opening your own bookmarks page) if
bookmarks stop showing up as expected. gallery-dl (already a dependency
of this project) was checked as a possible reference for this, but its
Pixiv support goes through the separate mobile-app OAuth API instead of
PHPSESSID-based ajax calls, so it wasn't directly reusable here.

Resolving "my own user id" specifically (resolve_own_user_id, below)
uses a different, longer-established convention instead: pixiv.net embeds
the logged-in user's info as JSON in a `<meta id="meta-global-data">` tag
on every page -- this is not a dedicated API endpoint, just what's already
present in the HTML of any page load.
"""
import html
import json
import logging
import re

import requests

logger = logging.getLogger(__name__)

_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
)
_PAGE_SIZE = 48
_META_RE = re.compile(r'<meta\s+name="global-data"\s+id="meta-global-data"\s+content="([^"]*)"')


class PixivAuthError(RuntimeError):
    """Raised when Pixiv rejects the session (expired/invalid PHPSESSID)."""


class PixivAPIError(RuntimeError):
    """Raised for any other unexpected API/HTTP failure."""


def _get_phpsessid() -> str:
    from .pixiv_creds import get_credentials

    return get_credentials()['phpsessid']


def resolve_own_user_id() -> str:
    """The logged-in account's numeric user id -- needed since the
    bookmarks endpoint is per-user, not "my own" implicitly. See this
    module's docstring for where this comes from (a meta tag on any
    pixiv.net page load, not a dedicated API call).

    Raises:
        PixivAuthError: not logged in / session invalid
        PixivAPIError:  page didn't load, or its expected structure wasn't found
        RuntimeError:   PHPSESSID not configured
    """
    phpsessid = _get_phpsessid()
    if not phpsessid:
        raise RuntimeError('PIXIV_PHPSESSIDが設定されていません')

    resp = requests.get(
        'https://www.pixiv.net/', cookies={'PHPSESSID': phpsessid}, headers={'User-Agent': _UA}, timeout=15,
    )
    if resp.status_code != 200:
        raise PixivAPIError(f'HTTP {resp.status_code}')

    m = _META_RE.search(resp.text)
    if not m:
        raise PixivAPIError('ページ内にユーザー情報が見つかりませんでした(ページ構造が変わった可能性)')

    try:
        data = json.loads(html.unescape(m.group(1)))
    except ValueError as e:
        raise PixivAPIError(f'ユーザー情報の解析に失敗しました: {e}') from e

    user_data = data.get('userData')
    if not user_data or not user_data.get('id'):
        raise PixivAuthError('Pixivセッションが無効です(PHPSESSIDの期限切れの可能性)')

    return str(user_data['id'])


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
    phpsessid = _get_phpsessid()
    if not phpsessid:
        raise RuntimeError('PIXIV_PHPSESSIDが設定されていません')

    candidates = []
    offset = start_offset
    for _ in range(max_pages):
        resp = requests.get(
            f'https://www.pixiv.net/ajax/user/{user_id}/illusts/bookmarks',
            params={'tag': '', 'offset': offset, 'limit': _PAGE_SIZE, 'rest': 'show'},
            cookies={'PHPSESSID': phpsessid}, headers={'User-Agent': _UA}, timeout=15,
        )
        if resp.status_code == 401:
            raise PixivAuthError('Pixivセッションが無効です(PHPSESSIDの期限切れの可能性)')
        if resp.status_code != 200:
            raise PixivAPIError(f'HTTP {resp.status_code}')

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
