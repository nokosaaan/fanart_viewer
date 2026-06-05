"""yt-dlp based media fetcher for Twitter/X.

Uses yt-dlp's Python API to extract and download tweet media.
More reliable than raw Playwright for authenticated Twitter content
because yt-dlp mimics a real browser session rather than automating one.
"""

import os
import tempfile
import logging

logger = logging.getLogger(__name__)


def _build_cookies_file(auth_token: str, ct0: str | None = None) -> str:
    """Write a Netscape-format cookies.txt to a temp file and return its path."""
    lines = ['# Netscape HTTP Cookie File\n']
    for domain in ('.twitter.com', '.x.com'):
        secure = 'TRUE'
        http_only_flag = 'TRUE'
        lines.append(f'{domain}\tTRUE\t/\t{secure}\t0\tauth_token\t{auth_token}\n')
        if ct0:
            lines.append(f'{domain}\tTRUE\t/\t{secure}\t0\tct0\t{ct0}\n')

    fd, path = tempfile.mkstemp(suffix='.txt', prefix='tw_cookies_')
    try:
        with os.fdopen(fd, 'w') as f:
            f.writelines(lines)
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise
    return path


def fetch_twitter_media_ytdlp(url: str) -> list[tuple[bytes, str]]:
    """Fetch media from a tweet URL using yt-dlp.

    Returns a list of (image_bytes, mime_type) tuples.
    Raises RuntimeError if yt-dlp is not available or fetch fails.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError('yt-dlp not installed; run pip install yt-dlp')

    auth_token = os.environ.get('TWITTER_AUTH_TOKEN')
    ct0 = os.environ.get('TWITTER_CT0')

    if not auth_token:
        raise RuntimeError('TWITTER_AUTH_TOKEN not set')

    cookies_path = _build_cookies_file(auth_token, ct0)
    results = []

    try:
        import requests as _requests

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,      # extract info only, don't download
            'cookiefile': cookies_path,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return []

        # Collect image URLs from the extracted info.
        # Tweets can have multiple photos; each appears as a separate format
        # with vcodec='none' (image-only) or in info['thumbnails'].
        media_urls = []

        formats = info.get('formats') or []
        for fmt in formats:
            ext = (fmt.get('ext') or '').lower()
            if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
                u = fmt.get('url')
                if u:
                    media_urls.append(u)

        # For multi-photo tweets, yt-dlp may return entries
        entries = info.get('entries') or []
        for entry in entries:
            for fmt in (entry.get('formats') or []):
                ext = (fmt.get('ext') or '').lower()
                if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
                    u = fmt.get('url')
                    if u and u not in media_urls:
                        media_urls.append(u)
            # also check thumbnail of each entry
            thumb = entry.get('thumbnail') or entry.get('url')
            if thumb and 'pbs.twimg.com' in thumb and thumb not in media_urls:
                media_urls.append(thumb)

        # Fallback: use thumbnail if no image formats found
        if not media_urls:
            thumb = info.get('thumbnail')
            if thumb and 'pbs.twimg.com' in thumb:
                media_urls.append(thumb)

        # Upgrade size params and skip profile images
        import re
        cleaned = []
        for u in media_urls:
            if '/profile_images/' in u:
                continue
            u = re.sub(r'(?<=[?&])name=(?:small|medium|thumb|360x360|240x240)', 'name=large', u)
            if u not in cleaned:
                cleaned.append(u)

        # Download each image URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Cookie': f'auth_token={auth_token}' + (f'; ct0={ct0}' if ct0 else ''),
            'Referer': 'https://x.com/',
        }
        for img_url in cleaned:
            try:
                r = _requests.get(img_url, headers=headers, timeout=20, allow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get('content-type', 'image/jpeg')
                    mime = ct.split(';', 1)[0].lower()
                    if mime.startswith('image') and mime != 'image/svg+xml':
                        results.append((r.content, mime))
            except Exception as e:
                logger.warning('yt-dlp fetch: failed to download %s: %s', img_url, e)
                continue

    finally:
        try:
            os.unlink(cookies_path)
        except Exception:
            pass

    return results
