"""Recover a DELETED Pixiv artwork's original image(s) straight from
Pixiv's CDN, when nothing else in this app can help: fetch_and_save_preview
needs the post to still be live (via gallery-dl/the ajax API/Playwright),
and item.upload_preview needs the user to already have the file saved
somewhere -- this covers the third case, where the post is gone and the
user never saved a copy, but the file MIGHT still exist on Pixiv's CDN
(which doesn't always purge immediately on deletion).

Ported from (and credit to) the public domain-ish "Pixiv Salvage Tool"
gitlab snippet (https://gitlab.com/-/snippets/4893870, by kumita435675),
which does the URL-discovery half of this; this module adds the second
half (actually downloading the discovered URLs into bytes this app can
store as PreviewImage rows -- see views.ItemViewSet.salvage_pixiv).

How it works (no login/session needed at all -- just a Referer header):

Pixiv's CDN URL for an artwork's Nth page encodes the exact upload
timestamp: https://i.pximg.net/img-original/img/{Y/m/d/H/M/S}/{id}_p{n}.{ext}
For a deleted artwork that timestamp is unknown, but pixiv illust ids are
assigned in strict global upload order, so:
  1. Walk downward from `artwork_id - 1` and upward from `artwork_id + 1`,
     one id at a time, asking pixiv's own /ajax/illust/{id} endpoint for
     THAT id's own upload timestamp (via its `userIllusts` self-entry --
     works for ANY currently-existing id, regardless of whose work it is)
     until a still-existing neighbor is found on each side. Those two
     neighbors' timestamps bracket the deleted work's own true timestamp
     (id order and upload order are the same order).
  2. Brute-force every second in that bracket (across jpg/png/gif, in
     that preference order -- mixing extensions within one post makes
     pixiv normalize everything to jpg) via lightweight HEAD requests
     against the reconstructed CDN URL, until one returns 200.
  3. Once page 0 is found, walk _p1, _p2, ... the same way to pick up
     every page of a multi-image post.

This is fundamentally a brute-force search, so unlike everything else in
this app it can take a genuinely long time in the worst case: if the nearby
still-existing ids happen to be uploaded far apart in time, the bracket to
search can be large. MAX_BRACKET_SECONDS below is this module's own
addition (not in the original snippet) -- refuses up front rather than
silently grinding for hours if the bracket is unreasonably wide.
"""
import itertools
import logging
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

_ARTWORKS_DETAIL_ENDPOINT = 'https://www.pixiv.net/ajax/illust/'
_ARTWORKS_EXT_LIST = ('jpg', 'png', 'gif')  # mixing extensions within one post gets normalized to jpg
_ARTWORKS_TEMPLATE = 'https://i.pximg.net/img-original/img/{timestamp}/{id}_p{number}.{ext}'
_HEADERS = {
    'Referer': 'https://www.pixiv.net/',
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 '
        '(KHTML, like Gecko) CriOS/135.0.7049.53 Mobile/15E148 Safari/604.1'
    ),
}
_CONTENT_TYPE_BY_EXT = {'jpg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif'}

# Safety cap not present in the original snippet: refuses immediately if
# the bracket between the two found neighbors is wider than this, rather
# than silently spending (bracket_seconds / interval) * 3 extensions worth
# of HEAD requests -- which for a multi-hour bracket could genuinely take
# hours and hammer pixiv's CDN the whole time.
MAX_BRACKET_SECONDS = 30 * 60  # 30 minutes


class PixivSalvageError(RuntimeError):
    """Raised when salvage can't proceed or finds nothing."""


def _get_artwork_timestamp(session: requests.Session, artwork_id: int) -> str | None:
    """This id's own upload timestamp (ISO 8601), via its self-entry in
    pixiv's own /ajax/illust/{id} 'userIllusts' response -- works for any
    currently-existing id. None if this id doesn't exist (deleted, or
    never existed)."""
    try:
        resp = session.get(f'{_ARTWORKS_DETAIL_ENDPOINT}{artwork_id}', timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    try:
        user_illusts = resp.json().get('body', {}).get('userIllusts', {})
    except ValueError:
        return None
    return user_illusts.get(str(artwork_id), {}).get('updateDate')


def _find_neighbor(session: requests.Session, start_id: int, step: int, max_attempts: int) -> tuple[int, str] | None:
    """Nearest still-existing id in `step` direction from `start_id`, and
    its own upload timestamp. None if nothing found within max_attempts."""
    current_id = start_id
    for _ in range(max_attempts):
        timestamp = _get_artwork_timestamp(session, current_id)
        if timestamp is not None:
            return current_id, timestamp
        current_id += step
    return None


def _generate_timestamps(start: datetime, end: datetime):
    current = start
    while current <= end:
        yield current.strftime('%Y/%m/%d/%H/%M/%S')
        current += timedelta(seconds=1)


def _find_base_page(
    session: requests.Session, start: datetime, end: datetime, artwork_id: int, interval: float,
) -> tuple[str, str, str] | None:
    """(url, timestamp, ext) for page 0, brute-forcing every second in
    [start, end] across each extension in turn. None if nothing 200s."""
    for ext in _ARTWORKS_EXT_LIST:
        for ts in _generate_timestamps(start, end):
            url = _ARTWORKS_TEMPLATE.format(timestamp=ts, id=artwork_id, number=0, ext=ext)
            resp = session.head(url, timeout=10)
            if resp.status_code == 200:
                return url, ts, ext
            time.sleep(interval)
    return None


def _find_all_pages(
    session: requests.Session, artwork_id: int, timestamp: str, ext: str, interval: float,
) -> list[str]:
    """Every page URL (_p0, _p1, _p2, ...) that 200s, stopping at the
    first miss."""
    urls = []
    for i in itertools.count():
        url = _ARTWORKS_TEMPLATE.format(timestamp=timestamp, id=artwork_id, number=i, ext=ext)
        resp = session.head(url, timeout=10)
        if resp.status_code != 200:
            break
        urls.append(url)
        time.sleep(interval)
    return urls


def discover_salvage_urls(artwork_id: int, interval: float = 0.75, max_attempts: int = 30) -> list[str]:
    """Find every page's direct CDN image URL for a deleted Pixiv artwork.
    Raises PixivSalvageError if no bracketing neighbors could be found, if
    the bracket between them is unreasonably wide (see MAX_BRACKET_SECONDS),
    or if nothing 200s within the bracket."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    lower = _find_neighbor(session, artwork_id - 1, step=-1, max_attempts=max_attempts)
    if lower is None:
        raise PixivSalvageError(f'{artwork_id}より前の現存する投稿が見つかりませんでした')
    upper = _find_neighbor(session, artwork_id + 1, step=1, max_attempts=max_attempts)
    if upper is None:
        raise PixivSalvageError(f'{artwork_id}より後の現存する投稿が見つかりませんでした')

    start_dt = datetime.fromisoformat(lower[1])
    # pixiv's own /ajax/illust/{id} 'updateDate' is truncated to the START
    # of its minute (seconds always :00 -- confirmed live: several real,
    # still-existing, adjacent artwork ids in a row all reported the exact
    # same "...:00" value despite obviously not having been uploaded in the
    # same literal second). The upper neighbor's TRUE upload instant could
    # be anywhere within that reported minute, so its floored value alone
    # is not a safe upper bound -- widening to the end of that same minute
    # is. Without this, a real live test against a genuine (non-deleted,
    # used only to verify the algorithm) artwork whose two neighbors both
    # floored to the SAME minute as the artwork itself produced a
    # single-second bracket that did not contain the artwork's actual
    # second and salvage failed outright, even though the file was right
    # there on the CDN.
    end_dt = datetime.fromisoformat(upper[1]) + timedelta(seconds=59)
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt
    bracket_seconds = (end_dt - start_dt).total_seconds()
    if bracket_seconds > MAX_BRACKET_SECONDS:
        raise PixivSalvageError(
            f'前後の現存投稿の間隔が広すぎるため中断しました({int(bracket_seconds)}秒 > 上限{MAX_BRACKET_SECONDS}秒)。'
            '投稿日時の推定が困難で、実行に非常に時間がかかる見込みです。'
        )

    base = _find_base_page(session, start_dt, end_dt, artwork_id, interval)
    if base is None:
        raise PixivSalvageError('CDN上に画像が見つかりませんでした(完全に削除済みの可能性)')
    _, timestamp, ext = base

    return _find_all_pages(session, artwork_id, timestamp, ext, interval)


def fetch_salvaged_images(urls: list[str]) -> list[tuple[bytes, str]]:
    """Download each discovered URL's actual bytes. Returns
    [(content_bytes, content_type), ...] in the same order, skipping (not
    raising on) any individual download failure -- a partial recovery is
    still worth keeping."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    results = []
    for url in urls:
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning('pixiv_salvage: failed to download %s', url)
            continue
        ext = url.rsplit('.', 1)[-1].lower()
        content_type = _CONTENT_TYPE_BY_EXT.get(ext, 'image/jpeg')
        results.append((resp.content, content_type))
    return results
