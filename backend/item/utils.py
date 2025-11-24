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
        'media.fields': 'type,url,preview_image_url,media_url_https'
    }
    headers = {'Authorization': f'Bearer {bearer}'}
    try:
        r = requests.get(api, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        j = r.json()
        # store raw response for debugging
        try:
            LAST_TW_API_RESP[tweet_id] = j
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
        return None

    results: List[str] = []
    # Try meta tags first (og:image)
    if BeautifulSoup:
        soup = BeautifulSoup(html, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            results.append(urljoin(tweet_url, og.get('content')))
        tw = soup.find('meta', attrs={'name': 'twitter:image'})
        if tw and tw.get('content'):
            results.append(urljoin(tweet_url, tw.get('content')))

        # collect image tags that look like tweet media
        imgs = [img.get('src') for img in soup.find_all('img') if img.get('src')]
        for src in imgs:
            if 'pbs.twimg.com' in src or 'twimg' in src:
                full = urljoin(tweet_url, src)
                if full not in results:
                    results.append(full)

        # generic img collection as fallback; keep order deterministic
        for im in soup.find_all('img'):
            s = im.get('src')
            if s:
                full = urljoin(tweet_url, s)
                if full not in results:
                    results.append(full)

    # Fallback regex matches if nothing found yet
    if not results:
        matches = re.findall(r'https://pbs\.twimg\.com/media/[^"\s<>]+', html)
        for m in matches:
            full = urljoin(tweet_url, m)
            if full not in results:
                results.append(full)
    if not results:
        matches = re.findall(r'https?://[^"\s<>]*twimg[^"\s<>]+', html)
        for m in matches:
            full = urljoin(tweet_url, m)
            if full not in results:
                results.append(full)
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