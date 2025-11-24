import os
import re
import requests
from typing import List, Optional
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

# store last API JSON responses for debugging when requested
LAST_TW_API_RESP = {}


def _fetch_via_api(tweet_url: str) -> List[str]:
    """Use Twitter v2 API to return a list of media URLs (may be empty).

    Requires `TW_BEARER` in the environment. Returns [] on failure or
    when no media found.
    """
    parts = tweet_url.rstrip('/').split('/')
    tweet_id = parts[-1]
    bearer = os.environ.get('TW_BEARER')
    if not bearer:
        return []

    api = f'https://api.twitter.com/2/tweets/{tweet_id}'
    params = {
        'expansions': 'attachments.media_keys',
        # request a broad set of media fields; some fields may be absent depending on account/tweet
        'media.fields': 'media_key,type,url,preview_image_url,variants,alt_text,media_key'
    }
    headers = {'Authorization': f'Bearer {bearer}'}
    try:
        r = requests.get(api, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        j = r.json()
        # store raw response and status for debugging
        try:
            LAST_TW_API_RESP[tweet_id] = {'json': j, 'status': r.status_code}
        except Exception:
            pass
        # optional verbose logging when env var enabled
        if os.environ.get('TW_API_DEBUG'):
            try:
                import logging as _logging
                _logging.getLogger('fanart_viewer').info('Twitter API response for %s: %s', tweet_id, j)
            except Exception:
                pass
    except Exception:
        return []

    media = j.get('includes', {}).get('media', [])
    urls: List[str] = []
    for m in media:
        if m.get('type') == 'photo':
            # prefer explicit url fields
            for key in ('url', 'media_url_https', 'preview_image_url'):
                v = m.get(key)
                if v and v not in urls:
                    urls.append(v)
    return urls


def _fetch_via_scrape(tweet_url: str) -> List[str]:
    # Simple HTML scraping fallback. Works for many public tweets without login.
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
    }
    try:
        r = requests.get(tweet_url, headers=headers, timeout=8)
        r.raise_for_status()
        html = r.text
    except Exception:
        return []

    results: List[str] = []
    seen = set()

    def add(u: str):
        if not u:
            return
        # ignore inline/data URIs (these can be extremely large blobs)
        if u.strip().startswith('data:'):
            return
        full = urljoin(tweet_url, u)
        if full not in seen:
            seen.add(full)
            results.append(full)

    # Try meta tags first (og:image / twitter:image)
    if BeautifulSoup:
        soup = BeautifulSoup(html, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            add(og.get('content'))
        tw = soup.find('meta', attrs={'name': 'twitter:image'})
        if tw and tw.get('content'):
            add(tw.get('content'))

        # collect src/srcset/data-src/data-image-url/data-srcset attributes
        # Prefer collecting images within the app root first: many Twitter assets
        # (including gallery photos) are rendered under the element with id="react-root".
        # Collect all <img> tags under that node (src, srcset, data-src, data-image-url).
        root = soup.find(id='react-root')
        if root:
            for im in root.find_all('img'):
                s = im.get('src')
                if s:
                    add(s)
                ss = im.get('srcset') or im.get('data-srcset')
                if ss:
                    parts = [p.strip() for p in ss.split(',') if p.strip()]
                    for p in parts:
                        m = re.search(r'^(?P<url>[^\s]+)', p)
                        if m:
                            add(m.group('url'))
                ds = im.get('data-src') or im.get('data-image-url')
                if ds:
                    add(ds)

        # Collect other <img> tags on the page as a fallback (legacy logic).
        for im in soup.find_all('img'):
            # src
            s = im.get('src')
            if s:
                # prefer pbs.twimg.com and twimg images first
                if 'pbs.twimg.com' in s or 'twimg' in s or 'pic.twitter.com' in s:
                    add(s)
            # srcset: pick all entries
            ss = im.get('srcset') or im.get('data-srcset')
            if ss:
                parts = [p.strip() for p in ss.split(',') if p.strip()]
                for p in parts:
                    m = re.search(r'^(?P<url>[^\s]+)', p)
                    if m:
                        add(m.group('url'))
            # data-src / data-image-url
            ds = im.get('data-src') or im.get('data-image-url')
            if ds:
                add(ds)

        # picture/figure fallback
        for pic in soup.find_all('picture'):
            for im in pic.find_all('img'):
                if im.get('src'):
                    add(im.get('src'))
                ss = im.get('srcset')
                if ss:
                    for p in ss.split(','):
                        m = re.search(r'^(?P<url>[^\s]+)', p.strip())
                        if m:
                            add(m.group('url'))

        # anchors that may point to pic.twitter.com shortlinks
        for a in soup.find_all('a'):
            href = a.get('href')
            if href and 'pic.twitter.com' in href:
                add(href)

    # Regex-based fallbacks to catch direct pbs.twimg links
    matches = re.findall(r'https?://pbs\.twimg\.com/media/[^"\s<>]+', html)
    for m in matches:
        add(m)
    matches = re.findall(r'https?://[^"\s<>]*twimg[^"\s<>]+', html)
    for m in matches:
        add(m)

    # Also inspect inline <script> blocks for embedded JSON containing
    # media URLs (keys like media_url, media_url_https, preview_image_url)
    if BeautifulSoup:
        for script in soup.find_all('script'):
            try:
                script_text = script.string or script.get_text() or ''
            except Exception:
                script_text = ''
            if not script_text:
                continue
            # look for JSON-style keys that reference pbs.twimg.com
            for m in re.findall(r'"media_url_https"\s*:\s*"(https?://pbs\.twimg\.com/[^"]+)"', script_text):
                add(m)
            for m in re.findall(r'"media_url"\s*:\s*"(https?://pbs\.twimg\.com/[^"]+)"', script_text):
                add(m)
            for m in re.findall(r'"preview_image_url"\s*:\s*"(https?://pbs\.twimg\.com/[^"]+)"', script_text):
                add(m)
            # generic pbs links inside scripts
            for m in re.findall(r'https?://pbs\.twimg\.com/media/[^"\s<>\)]+', script_text):
                add(m)

    # Resolve pic.twitter.com shortlinks by following redirects (cheap HEAD/GET)
    resolved = []
    for u in list(results):
        if 'pic.twitter.com' in u:
            try:
                r = requests.get(u, headers=headers, timeout=8, allow_redirects=True)
                if r.status_code in (200, 301, 302) and r.url:
                    final = r.url
                    # If redirect ended on pbs.twimg.com or twimg host, include
                    if 'pbs.twimg.com' in final or 'twimg' in final:
                        add(final)
            except Exception:
                continue

    return results


def _fetch_via_nitter(tweet_url: str) -> List[str]:
    # Use a Nitter instance as an alternative front-end. Configure via NITTER_BASE env var.
    nitter_base = os.environ.get('NITTER_BASE', 'https://nitter.net').rstrip('/')
    # convert https://twitter.com/user/status/ID to nitter URL
    parts = tweet_url.split('/')
    if len(parts) < 5:
        return None
    user = parts[3]
    tweet_id = parts[-1]
    nitter_status = f"{nitter_base}/{user}/status/{tweet_id}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(nitter_status, headers=headers, timeout=8)
        r.raise_for_status()
        html = r.text
    except Exception:
        return None

    results: List[str] = []
    if BeautifulSoup:
        soup = BeautifulSoup(html, 'html.parser')
        imgs = [img.get('src') for img in soup.find_all('img') if img.get('src')]
        for src in imgs:
            full = urljoin(nitter_status, src)
            if full and ("pbs.twimg.com" in full or full.startswith('http')) and full not in results:
                results.append(full)

    if not results:
        matches = re.findall(r'https?://[^"\s<>]+', html)
        for m in matches:
            if 'pbs.twimg.com' in m or 'pic.' in m:
                if m not in results:
                    results.append(m)
    return results


def fetch_twitter_media_urls(tweet_url: str) -> List[str]:
    """
    Unified fetch function. Selection order depends on `TW_FETCH_METHOD` env var:
      - 'api' (default): use v2 API with `TW_BEARER` (fast/accurate but rate-limited)
      - 'scrape': fetch tweet HTML and parse for media
      - 'nitter': fetch via Nitter instance (configure `NITTER_BASE`)

    If the chosen method fails, function will try sensible fallbacks.
    """
    method = os.environ.get('TW_FETCH_METHOD', 'api').lower()

    # build try order
    if method == 'api':
        try_order = ['api', 'scrape', 'nitter']
    elif method == 'scrape':
        try_order = ['scrape', 'api', 'nitter']
    elif method == 'nitter':
        try_order = ['nitter', 'scrape', 'api']
    else:
        try_order = [method, 'api', 'scrape', 'nitter']

    collected: List[str] = []
    for m in try_order:
        try:
            if m == 'api':
                urls = _fetch_via_api(tweet_url)
            elif m == 'scrape':
                urls = _fetch_via_scrape(tweet_url)
            elif m == 'nitter':
                urls = _fetch_via_nitter(tweet_url)
            else:
                urls = []
        except Exception:
            urls = []
        if not urls:
            continue
        # extend preserving order and uniqueness
        for u in urls:
            if u and u not in collected:
                collected.append(u)
        # continue trying other methods to aggregate additional candidates
        # (some methods may return non-media hits like emoji; collecting
        # from multiple backends improves coverage)
    return collected


def get_last_api_response(tweet_url: str):
    """Return the last stored API JSON for the given tweet URL, if available."""
    try:
        parts = tweet_url.rstrip('/').split('/')
        tweet_id = parts[-1]
        return LAST_TW_API_RESP.get(tweet_id)
    except Exception:
        return None


def fetch_twitter_media_urls_with_sources(tweet_url: str) -> List[tuple]:
    """
    Like `fetch_twitter_media_urls` but returns a list of (url, method)
    tuples so callers can know which backend produced each candidate.
    """
    method = os.environ.get('TW_FETCH_METHOD', 'api').lower()
    if method == 'api':
        try_order = ['api', 'scrape', 'nitter']
    elif method == 'scrape':
        try_order = ['scrape', 'api', 'nitter']
    elif method == 'nitter':
        try_order = ['nitter', 'scrape', 'api']
    else:
        try_order = [method, 'api', 'scrape', 'nitter']

    collected = []
    seen = set()
    for m in try_order:
        try:
            if m == 'api':
                urls = _fetch_via_api(tweet_url)
            elif m == 'scrape':
                urls = _fetch_via_scrape(tweet_url)
            elif m == 'nitter':
                urls = _fetch_via_nitter(tweet_url)
            else:
                urls = []
        except Exception:
            urls = []
        if not urls:
            continue
        for u in urls:
            if u and u not in seen:
                collected.append((u, m))
                seen.add(u)

    return collected


def fetch_twitter_media_url(tweet_url: str) -> Optional[str]:
    """Backward-compatible wrapper that returns the first candidate or None."""
    urls = fetch_twitter_media_urls(tweet_url)
    return urls[0] if urls else None