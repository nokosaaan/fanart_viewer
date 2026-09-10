"""Poipiku bookmark ("お気に入り") list scanner -- mirrors
item.pixiv_bookmarks_fetch's own shape (discovery via known_ids +
resume-across-ticks), but for https://poipiku.com/MyBookmarkListPcV.jsp
instead of Pixiv's ajax bookmarks endpoint.

Unlike Twitter/Pixiv, there's no separate "resolve my own account id"
step needed at all: the bookmark list page itself works from just the
session cookie (POIPIKU_LK/JSESSIONID -- see item.poipiku_creds), and
the page's own pagination links (<nav class="PageBar">) already embed
whatever numeric account id the server resolved, so the next page's URL
is always read directly off the CURRENT page's own HTML rather than
being constructed here. Confirmed live against a real saved bookmark
list page (see conversation): a page with only one page of results has
its prev/current/next PageBar links all pointing at the same `PG=`
value -- that's poipiku's own way of saying "there is no other page"
(no disabled/hidden state), which _find_next_page_url below relies on.
"""
import logging
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_BASE_URL = 'https://poipiku.com/MyBookmarkListPcV.jsp'
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}
# https://poipiku.com/{user_id}/{illust_id}.html
_ILLUST_URL_RE = re.compile(r'poipiku\.com/(\d+)/(\d+)\.html')
_PG_RE = re.compile(r'[?&]PG=(\d+)')


class PoipikuBookmarksError(RuntimeError):
    """Raised for any unexpected HTTP/parse failure fetching the bookmark list."""


def _session():
    import requests as _requests

    from .poipiku_creds import get_credentials

    creds = get_credentials()
    if not creds['lk'] and not creds['jsessionid']:
        raise RuntimeError('Poipiku認証情報が設定されていません(ヘッダーメニューの「Poipiku 認証情報」から設定してください)')

    session = _requests.Session()
    session.headers.update(_HEADERS)
    cookie_parts = []
    if creds['lk']:
        cookie_parts.append(f"POIPIKU_LK={creds['lk']}")
    if creds['jsessionid']:
        cookie_parts.append(f"JSESSIONID={creds['jsessionid']}")
    session.headers['Cookie'] = '; '.join(cookie_parts)
    return session


def _find_next_page_url(soup, current_url: str) -> str | None:
    """The PageBar's own "next" (chevron-right) link, or None if this is
    the last page -- see this module's own docstring for how poipiku
    signals "no next page" (the link points at the SAME page instead of
    being absent/disabled)."""
    nav = soup.find('nav', class_='PageBar')
    if nav is None:
        return None
    links = nav.find_all('a', class_='PageBarItem')
    if not links:
        return None
    next_link = links[-1]  # chevron-right is always the last PageBarItem
    href = next_link.get('href')
    if not href:
        return None
    next_url = urljoin(current_url, href)

    current_pg_match = _PG_RE.search(current_url)
    next_pg_match = _PG_RE.search(next_url)
    current_pg = int(current_pg_match.group(1)) if current_pg_match else 0
    next_pg = int(next_pg_match.group(1)) if next_pg_match else 0
    if next_pg <= current_pg:
        return None  # looped back to the same (or an earlier) page -- no more pages
    return next_url


def _parse_bookmarks_page(html_text: str, page_url: str):
    """Returns (candidates, next_page_url). Each candidate:
    {'illust_id': int, 'user_id': str, 'user_name': str, 'url': str, 'description': str}
    newest-first (the page's own natural order)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, 'html.parser')
    candidates = []
    for thumb in soup.find_all('div', class_='IllustThumb'):
        img_link = thumb.find('a', class_='IllustThumbImg') or thumb.find('a', class_='IllustInfo')
        href = img_link.get('href') if img_link else None
        if not href:
            continue
        m = _ILLUST_URL_RE.search(urljoin(page_url, href))
        if not m:
            continue
        user_id, illust_id = m.group(1), m.group(2)

        user_link = thumb.find('a', class_='IllustUser')
        user_name_tag = user_link.find('h2', class_='IllustUserName') if user_link else None
        user_name = user_name_tag.get_text(strip=True) if user_name_tag else ''

        desc_tag = thumb.find('span', class_='IllustInfoDesc')
        description = desc_tag.get_text(strip=True) if desc_tag else ''

        candidates.append({
            'illust_id': int(illust_id),
            'user_id': user_id,
            'user_name': user_name,
            'url': f'https://poipiku.com/{user_id}/{illust_id}.html',
            'description': description,
        })

    next_page_url = _find_next_page_url(soup, page_url)
    return candidates, next_page_url


def fetch_account_bookmarks(known_ids: set, max_pages: int = 1, start_url: str | None = None):
    """Scan the logged-in account's own bookmark list ("お気に入り"),
    newest-first, returning (candidates, next_page_url). Stops as soon as
    an already-known illust is seen on some page (same early-stop logic
    as Twitter/Pixiv's own bookmark scans), or after `max_pages`,
    whichever comes first.

    `next_page_url` resumes a still-catching-up scan across ticks (mirrors
    PixivPollState.resume_offset's own role): '' once caught up (nothing
    to resume, or no more pages at all), otherwise the exact URL to
    continue from next tick -- taken directly from the page's own PageBar
    link, not reconstructed.

    Raises PoipikuBookmarksError on any HTTP/parse failure.
    """
    session = _session()
    candidates = []
    page_url = start_url or _BASE_URL

    for _ in range(max_pages):
        try:
            resp = session.get(page_url, timeout=20)
        except Exception as e:
            raise PoipikuBookmarksError(f'お気に入り一覧の取得に失敗しました: {e}') from e
        if resp.status_code != 200:
            raise PoipikuBookmarksError(f'HTTP {resp.status_code}')

        page_candidates, next_page_url = _parse_bookmarks_page(resp.text, page_url)
        if not page_candidates:
            return candidates, ''  # empty page -- nothing more to backfill

        caught_up = False
        for cand in page_candidates:
            if cand['illust_id'] in known_ids:
                caught_up = True
                break
            candidates.append(cand)

        if caught_up or not next_page_url:
            return candidates, ''

        page_url = next_page_url

    return candidates, page_url
