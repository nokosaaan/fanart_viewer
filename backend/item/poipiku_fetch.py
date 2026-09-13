"""Poipiku media fetcher.

Fetches all images from a Poipiku work URL (https://poipiku.com/{user_id}/{illust_id}/).
- Parses IllustItemThumbImg from the initial page HTML.
- Calls the ShowAppendFile AJAX endpoint to retrieve images not yet in the DOM.
- Strips the _640.jpg thumbnail suffix to get original-resolution URLs.

NOTE: the work-detail lightbox's <img class="DetailIllustItemImage"> (a
signed CloudFront URL, seen in browser devtools once a thumbnail is
clicked) is NOT present in the plain server-rendered HTML this module
fetches — confirmed via a raw `curl`/view-source dump, which showed no such
element at all. It's populated by JavaScript after the click (likely its
own AJAX call, not yet identified), so a plain requests+BeautifulSoup fetch
can't reach it and this module doesn't try to.

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

# Generic "click to reveal" graphics Poipiku serves as IllustItemThumbImg in
# place of the real image — for R18/R15 works pending the site's own age
# gate (/img/R-18.png) as well as e.g. "AI学習禁止" (AI-training-opt-out)
# works (/img/warning.png) — regardless of login/cookie state. A real
# browser reveals the actual image by calling showIllustDetail() on click
# (see _fetch_illust_detail below); a plain fetch of these placeholder URLs
# just returns the small generic graphic itself.
_PLACEHOLDER_THUMB_RE = re.compile(r'/img/(?:r-18|warning)\.png', re.IGNORECASE)


def _thumb_to_original(thumb_url: str) -> str:
    # https://cdn.poipiku.com/UUUU/IIII_hash.png_640.jpg → …/IIII_hash.png
    return re.sub(r'_\d+\.jpg$', '', thumb_url)


def _is_artwork_img(src: str) -> bool:
    if not src or 'cdn.poipiku.com' not in src:
        return False
    return not any(skip in src for skip in _CDN_SKIP)


def _is_placeholder_thumb(src: str) -> bool:
    return bool(_PLACEHOLDER_THUMB_RE.search(src or ''))


def _collect_gated_ad_indices(container) -> list[int]:
    """Find placeholder-gated IllustItemThumbImg elements in a BeautifulSoup
    node and return the distinct `AD` index (the 3rd arg of the enclosing
    <a onclick="showIllustDetail(UID, IID, AD)">) each needs, so the real
    image behind each can be fetched via ShowIllustDetailF.jsp."""
    indices: list[int] = []
    seen: set[int] = set()
    for img in container.find_all('img', class_='IllustItemThumbImg'):
        if not _is_placeholder_thumb(img.get('src', '')):
            continue
        link = img.find_parent('a', class_='IllustItemThumb')
        ad = -1
        if link:
            m = re.search(r'showIllustDetail\(\s*\d+\s*,\s*\d+\s*,\s*(-?\d+)\s*\)', link.get('onclick', ''))
            if m:
                ad = int(m.group(1))
        if ad not in seen:
            indices.append(ad)
            seen.add(ad)
    return indices


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


def _fetch_append_file_html(session, user_id: str, illust_id: str, referer: str,
                             pas: str = '') -> str:
    """Call the generateShowAppendFile AJAX endpoint and return the raw HTML
    fragment of additional thumbnails it reveals (caller parses it).

    Endpoint discovered from /assets/js/common-134.js:
      POST /f/ShowAppendFileF.jsp  {UID, IID, PAS, MD, TWF}
    Response JSON: {result_num: N, html: '<img ...>...'}
    """
    try:
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
            return ''
        try:
            data = resp.json()
        except Exception:
            return ''
        return data.get('html') or ''
    except Exception as exc:
        logger.warning('poipiku ShowAppendFileF failed: %s', exc)
        return ''


def _fetch_illust_detail(session, user_id: str, illust_id: str, referer: str,
                          ad: int, pas: str = '') -> list[str]:
    """Call the showIllustDetail() AJAX endpoint (fired on a real thumbnail
    click) and return the real, signed CDN image URL(s) it reveals.

    Endpoint discovered from /assets/js/common-*.js (same place as
    ShowAppendFileF, see module docstring):
      POST /f/ShowIllustDetailF.jsp  {ID, TD, AD, PAS}
    Response JSON: {result: 1, html: '<img class="DetailIllustItemImage" src="...signed...">'}
    """
    try:
        resp = session.post(
            'https://poipiku.com/f/ShowIllustDetailF.jsp',
            data={'ID': user_id, 'TD': illust_id, 'AD': ad, 'PAS': pas},
            headers={
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Referer': referer,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug('poipiku ShowIllustDetailF HTTP %s (AD=%s)', resp.status_code, ad)
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        if data.get('result') != 1:
            return []
        html_frag = data.get('html') or ''
        return re.findall(r'DetailIllustItemImage"\s+src="([^"]+)"', html_frag)
    except Exception as exc:
        logger.warning('poipiku ShowIllustDetailF failed (AD=%s): %s', ad, exc)
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
        # Being logged in isn't enough for R18/R15 works: Poipiku also gates
        # them behind a separate "are you 18+?" confirmation
        # (showSwitchContentsViewModeDlg() in common-*.js), which just sets
        # this cookie via POST /f/SwitchContentsViewModeF.jsp {MD: 1} and
        # reloads. It's a plain site-wide preference flag (not a per-account
        # secret, no signature), so sending it directly skips needing to
        # replay that call. Without it, IllustItemThumbImg (and the
        # ShowIllustDetailF/ShowAppendFileF AJAX responses) all keep
        # returning the generic https://cdn.poipiku.com/img/R-18.png
        # placeholder graphic instead of the real per-work image, even with
        # valid login cookies — confirmed by comparing an authenticated
        # fetch with and without this cookie against a real R18 post.
        cookie_parts.append('POIPIKU_CONTENTS_VIEW_MODE=1')
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

    logger.info('poipiku: fetching %s (user_id=%s illust_id=%s, cookies=%s)',
                page_url, user_id, illust_id,
                'yes' if cookie_parts else 'no')

    page_resp = session.get(page_url, timeout=20, headers={'Referer': 'https://poipiku.com/'})
    page_resp.raise_for_status()
    logger.info('poipiku: page fetch HTTP %s, %d bytes', page_resp.status_code, len(page_resp.content))
    soup = BeautifulSoup(page_resp.text, 'html.parser')

    thumb_urls: list[str] = []
    seen: set[str] = set()

    def _add(urls):
        for u in urls:
            if u not in seen:
                thumb_urls.append(u)
                seen.add(u)

    # Real image URLs revealed via ShowIllustDetailF (see below) — already
    # signed CDN URLs, so downloaded as-is with no _thumb_to_original guessing.
    detail_urls: list[str] = []

    def _reveal_gated(container, pas: str):
        gated_ads = _collect_gated_ad_indices(container)
        if not gated_ads or not user_id:
            return
        logger.info('poipiku: %d placeholder-gated thumbnail(s) found, AD index(es)=%s — calling ShowIllustDetailF',
                    len(gated_ads), gated_ads)
        for ad in gated_ads:
            revealed = _fetch_illust_detail(session, user_id, illust_id, page_url, ad, pas=pas)
            logger.info('poipiku: ShowIllustDetailF(AD=%s) revealed %d real image URL(s): %s',
                        ad, len(revealed), revealed)
            for u in revealed:
                if u not in detail_urls:
                    detail_urls.append(u)

    if illust_id:
        item_div = soup.find(id=f'IllustItem_{illust_id}')
        logger.info('poipiku: IllustItem_%s div %s', illust_id, 'found' if item_div else 'NOT FOUND')
        if item_div:
            _add(_collect_from_soup(item_div))
            logger.info('poipiku: %d thumbnail(s) found directly in IllustItem div: %s',
                        len(thumb_urls), thumb_urls)

            pas_input = item_div.find('input', attrs={'name': 'PAS'})
            pas = (pas_input.get('value') or '') if pas_input else ''

            _reveal_gated(item_div, pas)

            # Check for ShowAppendFile button (may have display:none in static HTML)
            expand_btn = item_div.find('a', class_='IllustItemExpandBtn')
            if expand_btn and user_id:
                logger.info('poipiku: IllustItemExpandBtn found, calling ShowAppendFileF (PAS=%r)', pas)
                appended_html = _fetch_append_file_html(session, user_id, illust_id, page_url, pas=pas)
                if appended_html:
                    appended_soup = BeautifulSoup(appended_html, 'html.parser')
                    appended = _collect_from_soup(appended_soup)
                    logger.info('poipiku: ShowAppendFileF returned %d additional thumbnail(s): %s',
                                len(appended), appended)
                    _add(appended)
                    _reveal_gated(appended_soup, pas)
            else:
                logger.info('poipiku: no IllustItemExpandBtn found (single-image post, or button missing)')
        else:
            # IllustItem div not found on page; collect all artwork imgs as fallback
            _add(_collect_from_soup(soup))
            logger.info('poipiku: fallback whole-page scan found %d thumbnail(s): %s',
                        len(thumb_urls), thumb_urls)
    else:
        _add(_collect_from_soup(soup))
        logger.info('poipiku: no illust_id parsed from URL; whole-page scan found %d thumbnail(s): %s',
                    len(thumb_urls), thumb_urls)

    if not thumb_urls and not detail_urls:
        logger.warning('poipiku: no thumbnail URLs found at all for %s — returning empty', page_url)
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
        (rather than stopping at the first 200) is what lets a 403/warning
        response on the guessed original URL still fall back to the
        always-public _640.jpg thumbnail."""
        for cand_url in candidate_urls:
            try:
                r = session.get(cand_url, timeout=30, headers=dl_headers)
            except Exception as exc:
                logger.warning('poipiku: failed to download %s: %s', cand_url, exc)
                continue
            ct = r.headers.get('content-type', 'image/jpeg').split(';', 1)[0].lower()
            if r.status_code != 200:
                logger.info('poipiku: candidate %s -> HTTP %s, trying next candidate', cand_url, r.status_code)
                continue
            if not ct.startswith('image') or ct == 'image/svg+xml' or not r.content:
                logger.info('poipiku: candidate %s -> HTTP 200 but content-type=%s (not usable), trying next', cand_url, ct)
                continue
            if len(r.content) < _MIN_REAL_IMAGE_BYTES:
                logger.info('poipiku: candidate %s -> HTTP 200 but only %d byte(s) (< %d, likely a placeholder), trying next',
                            cand_url, len(r.content), _MIN_REAL_IMAGE_BYTES)
                continue
            logger.info('poipiku: candidate %s -> HTTP 200, %s, %d byte(s) — accepted', cand_url, ct, len(r.content))
            return r.content, ct
        logger.warning('poipiku: no candidate succeeded out of %s', candidate_urls)
        return None

    # Download images.  Try the guessed "_640.jpg suffix stripped" original
    # first (accessible when authenticated — POIPIKU_LK set), then the
    # always-public _640.jpg thumbnail as the final fallback.
    results: list[tuple[bytes, str]] = []
    for thumb_url in thumb_urls:
        candidates = [_thumb_to_original(thumb_url), thumb_url]
        downloaded = _try_download(candidates)
        if downloaded:
            results.append(downloaded)

    # detail_urls are already the real, signed image URL ShowIllustDetailF
    # handed back — no guessing/fallback needed, just download as-is.
    for detail_url in detail_urls:
        downloaded = _try_download([detail_url])
        if downloaded:
            results.append(downloaded)

    logger.info('poipiku: fetch_poipiku_media(%s) -> %d image(s) downloaded successfully out of %d candidate(s) found',
                url, len(results), len(thumb_urls) + len(detail_urls))

    return results
