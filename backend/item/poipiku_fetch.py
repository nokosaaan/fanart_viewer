"""Poipiku media fetcher.

Fetches all images from a Poipiku work URL (https://poipiku.com/{user_id}/{illust_id}/).
- Parses IllustItemThumbImg from the initial page HTML.
- Calls the ShowAppendFile AJAX endpoint to retrieve images not yet in the DOM.
- For the FIRST image only, also tries the work-detail lightbox's own
  <img class="DetailIllustItemImage"> src — a signed CloudFront URL
  Poipiku already serves at full resolution, present directly in the
  server-rendered page (see _collect_detail_image_url) — before falling
  back to guessing the original URL by stripping the _640.jpg thumbnail
  suffix, same as every other image.

Cookie authentication (for R15/R18/follower-only works):
  POIPIKU_LK         → sent as Cookie: POIPIKU_LK=<value>   (long-lived login key)
  POIPIKU_JSESSIONID → sent as Cookie: JSESSIONID=<value>   (session key, shorter-lived)
Both are optional; POIPIKU_LK alone is usually enough for R15 access.
Get both values from browser DevTools → Application → Cookies → https://poipiku.com.
"""

import os
import re
import logging

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}

_CDN_SKIP = ('/profile_', 'warning.png', '/assets/', '/img/warning')


def _thumb_to_original(thumb_url: str) -> str:
    # https://cdn.poipiku.com/UUUU/IIII_hash.png_640.jpg → …/IIII_hash.png
    return re.sub(r'_\d+\.jpg$', '', thumb_url)


def _is_artwork_img(src: str) -> bool:
    if not src or 'cdn.poipiku.com' not in src:
        return False
    return not any(skip in src for skip in _CDN_SKIP)


def _collect_from_soup(container) -> list[str]:
    """Return artwork thumbnail src values from a BeautifulSoup node."""
    urls = []
    seen = set()
    for img in container.find_all('img', class_='IllustItemThumbImg'):
        src = img.get('src', '')
        if _is_artwork_img(src) and src not in seen:
            urls.append(src)
            seen.add(src)
    return urls


def _collect_detail_image_url(soup) -> str | None:
    """The work-detail lightbox's own <img class="DetailIllustItemImage">
    src, when the page was fetched with a session that's actually allowed
    to see it — a signed CloudFront URL (?Expires=...&Key-Pair-Id=...) that
    Poipiku already serves at full resolution, unlike the public
    IllustItemThumbImg src (always a _640.jpg-suffixed thumbnail). Confirmed
    present directly in the server-rendered page HTML (not injected by JS
    afterward) via manual devtools inspection, so a plain requests+
    BeautifulSoup fetch can read it with no extra AJAX call needed.

    Only ever reflects whichever ONE image is showing in the lightbox by
    default for this fetch — for a multi-image post, the other images'
    equivalent signed URLs aren't confirmed reachable without further
    per-image interaction (unlike IllustItemThumbImg, where ALL of a post's
    thumbnails already appear in the static HTML, or via
    ShowAppendFileF.jsp for the rest — see _fetch_append_file). Callers
    should treat this as a best-quality candidate for the FIRST image only,
    keeping the existing thumbnail-based pipeline as-is for every other
    image and as this one's own fallback (see fetch_poipiku_media).
    """
    img = soup.find('img', class_='DetailIllustItemImage')
    if img is None:
        return None
    src = img.get('src', '')
    return src if src and 'cdn.poipiku.com' in src else None


def _fetch_append_file(session, user_id: str, illust_id: str, referer: str,
                        pas: str = '') -> list[str]:
    """Call the generateShowAppendFile AJAX endpoint and return thumbnail URLs.

    Endpoint discovered from /assets/js/common-134.js:
      POST /f/ShowAppendFileF.jsp  {UID, IID, PAS, MD, TWF}
    Response JSON: {result_num: N, html: '<img ...>...'}
    """
    try:
        from bs4 import BeautifulSoup

        resp = session.post(
            'https://poipiku.com/f/ShowAppendFileF.jsp',
            data={'UID': user_id, 'IID': illust_id, 'PAS': pas, 'MD': '0', 'TWF': '-1'},
            headers={
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Referer': referer,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug('poipiku ShowAppendFileF HTTP %s', resp.status_code)
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        html_frag = data.get('html') or ''
        if not html_frag:
            return []
        soup = BeautifulSoup(html_frag, 'html.parser')
        return _collect_from_soup(soup)
    except Exception as exc:
        logger.warning('poipiku ShowAppendFileF failed: %s', exc)
        return []


def fetch_poipiku_media(url: str) -> list[tuple[bytes, str]]:
    """Fetch all images from a Poipiku work URL.

    Returns [(image_bytes, mime_type), ...].
    Requires beautifulsoup4 and requests (both already in requirements.txt).
    Works without a session cookie for public content; set POIPIKU_PHPSESSID
    in the environment for age-restricted or follower-only works.
    """
    try:
        import requests as _requests
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(f'Missing dependency: {exc}')

    from .poipiku_creds import get_credentials as _get_poipiku_credentials
    _creds = _get_poipiku_credentials()
    poipiku_lk         = _creds['lk']
    poipiku_jsessionid = _creds['jsessionid']

    session = _requests.Session()
    session.headers.update(_HEADERS)

    # Build Cookie header directly — more reliable than session.cookies.set()
    # across requests versions and avoids domain-matching edge cases.
    cookie_parts = []
    if poipiku_lk:
        cookie_parts.append(f'POIPIKU_LK={poipiku_lk}')
    if poipiku_jsessionid:
        cookie_parts.append(f'JSESSIONID={poipiku_jsessionid}')
    if cookie_parts:
        session.headers['Cookie'] = '; '.join(cookie_parts)

    # Extract user_id / illust_id from URL.
    # Supported formats:
    #   https://poipiku.com/{user_id}/{illust_id}.html  (work detail page)
    #   https://poipiku.com/{user_id}/                  (user page)
    #   https://poipiku.com/{user_id}/?TD={illust_id}   (query-param variant)
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    # Match /{user_id}/{illust_id}.html
    m = re.search(r'/(\d+)/(\d+)\.html', parsed.path)
    if m:
        user_id = m.group(1)
        illust_id = m.group(2)
    else:
        path_parts = [p for p in parsed.path.split('/') if p.isdigit()]
        user_id = path_parts[0] if path_parts else None
        illust_id = path_parts[1] if len(path_parts) >= 2 else None

    # Query param TD overrides path-based illust_id
    if not illust_id and qs.get('TD'):
        illust_id = qs['TD'][0]

    # Use the work detail page directly when available (.html format), otherwise user page
    page_url = url if parsed.path.endswith('.html') else (
        f'https://poipiku.com/{user_id}/' if user_id else url
    )

    page_resp = session.get(page_url, timeout=20, headers={'Referer': 'https://poipiku.com/'})
    page_resp.raise_for_status()
    soup = BeautifulSoup(page_resp.text, 'html.parser')

    thumb_urls: list[str] = []
    seen: set[str] = set()

    def _add(urls):
        for u in urls:
            if u not in seen:
                thumb_urls.append(u)
                seen.add(u)

    if illust_id:
        item_div = soup.find(id=f'IllustItem_{illust_id}')
        if item_div:
            _add(_collect_from_soup(item_div))

            # Check for ShowAppendFile button (may have display:none in static HTML)
            expand_btn = item_div.find('a', class_='IllustItemExpandBtn')
            if expand_btn and user_id:
                pas_input = item_div.find('input', attrs={'name': 'PAS'})
                pas = (pas_input.get('value') or '') if pas_input else ''
                _add(_fetch_append_file(session, user_id, illust_id, page_url, pas=pas))
        else:
            # IllustItem div not found on page; collect all artwork imgs as fallback
            _add(_collect_from_soup(soup))
    else:
        _add(_collect_from_soup(soup))

    # Only confirmed to reflect the FIRST image of a post (see
    # _collect_detail_image_url's own docstring) — used as an extra, tried-
    # first candidate for thumb_urls[0] below. Computed BEFORE the
    # `not thumb_urls` bail-out below: a standalone work-detail page
    # (https://poipiku.com/{user_id}/{illust_id}.html) can render ONLY the
    # #DetailOverlay/DetailIllustItemImage structure with no
    # IllustItemThumbImg anywhere on it at all (that class is a listing-page
    # construct) — bailing out on empty thumb_urls before ever looking here
    # meant this fetcher returned nothing whatsoever for such a page, even
    # though the real image was sitting right there in the HTML.
    detail_img_url = _collect_detail_image_url(soup)

    if not thumb_urls:
        if detail_img_url:
            thumb_urls = [detail_img_url]
            # Prevent the loop below from ALSO adding it a second time as
            # thumb_urls[0]'s "extra" candidate.
            detail_img_url = None
        else:
            return []

    # A "tap to reveal"/access-warning placeholder graphic Poipiku serves in
    # place of real content (no session cookie, insufficient permission,
    # etc.) is a tiny icon — nowhere near the size of actual artwork. Same
    # signed-URL host either way (cdn.poipiku.com), so this is the only
    # cheap way to tell "got the real thing" from "got the warning" without
    # actually decoding the image.
    _MIN_REAL_IMAGE_BYTES = 1024

    dl_headers = {
        'Referer': page_url,
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    }

    def _try_download(candidate_urls):
        """First candidate that both responds 200 with an image content-type
        AND looks big enough to be real artwork, not a placeholder — see
        _MIN_REAL_IMAGE_BYTES above. Falling through to the next candidate
        (rather than stopping at the first 200) is what lets a signed
        detail-image URL that turns out to be a warning graphic still fall
        back to the existing thumbnail-based candidates below it."""
        for cand_url in candidate_urls:
            try:
                r = session.get(cand_url, timeout=30, headers=dl_headers)
            except Exception as exc:
                logger.warning('poipiku: failed to download %s: %s', cand_url, exc)
                continue
            if r.status_code != 200:
                continue
            ct = r.headers.get('content-type', 'image/jpeg').split(';', 1)[0].lower()
            if not ct.startswith('image') or ct == 'image/svg+xml' or not r.content:
                continue
            if len(r.content) < _MIN_REAL_IMAGE_BYTES:
                continue
            return r.content, ct
        return None

    # Download images.  For the first image, try the confirmed-full-
    # resolution detail-lightbox URL first (when found), then the guessed
    # "_640.jpg suffix stripped" original (accessible when authenticated —
    # POIPIKU_LK set), then the always-public _640.jpg thumbnail as the
    # final fallback. Every other image only has the latter two candidates
    # (see detail_img_url's own docstring for why).
    results: list[tuple[bytes, str]] = []
    for idx, thumb_url in enumerate(thumb_urls):
        candidates = []
        if idx == 0 and detail_img_url:
            candidates.append(detail_img_url)
        candidates.append(_thumb_to_original(thumb_url))
        candidates.append(thumb_url)

        downloaded = _try_download(candidates)
        if downloaded:
            results.append(downloaded)

    return results
