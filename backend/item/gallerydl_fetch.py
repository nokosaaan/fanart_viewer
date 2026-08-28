"""
gallery-dl based Twitter/X media fetcher.

gallery-dl is purpose-built for downloading from image galleries and has
maintained Twitter/X support including sensitive content. It handles all
cookie auth and API quirks internally.

Required env vars:
  TWITTER_AUTH_TOKEN  auth_token cookie from x.com
  TWITTER_CT0         ct0 cookie from x.com
"""

import json
import logging
import os
import subprocess
import tempfile

import requests

logger = logging.getLogger(__name__)

_DL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://x.com/",
}


def _write_cookies(auth_token: str, ct0: str) -> str:
    """Write a Netscape-format cookies file and return its path."""
    lines = ["# Netscape HTTP Cookie File\n"]
    for domain in (".twitter.com", ".x.com"):
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t0\tauth_token\t{auth_token}\n")
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t0\tct0\t{ct0}\n")
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="gdl_cookies_")
    with os.fdopen(fd, "w") as f:
        f.writelines(lines)
    return path


def _write_config(cookies_path: str) -> str:
    """Write a minimal gallery-dl config file and return its path."""
    cfg = {
        "extractor": {
            "twitter": {
                "cookies": cookies_path,
                # Fetch sensitive/NSFW content
                "sensitive": True,
                # Get original-size images
                "size": "orig",
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="gdl_config_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def fetch_twitter_media_gallerydl(url: str) -> list[tuple[bytes, str]]:
    """
    Fetch media from a tweet URL using gallery-dl.

    Returns list of (image_bytes, mime_type).
    Raises RuntimeError if gallery-dl is not installed or TWITTER_* vars are unset.
    """
    from .twitter_creds import get_credentials
    auth_token, ct0 = get_credentials()

    if not auth_token or not ct0:
        raise RuntimeError("TWITTER_AUTH_TOKEN または TWITTER_CT0 が未設定")

    cookies_path = config_path = None
    try:
        cookies_path = _write_cookies(auth_token, ct0)
        config_path = _write_config(cookies_path)

        # -g / --get-urls: print URLs to stdout instead of downloading
        proc = subprocess.run(
            [
                "gallery-dl",
                "--config", config_path,
                "--get-urls",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if proc.returncode not in (0, 1):
            # returncode 1 = partial success (some URLs found but errors occurred)
            logger.warning("gallery-dl exited %s for %s: %s", proc.returncode, url, proc.stderr[:300])

        image_urls = [
            line.strip()
            for line in proc.stdout.splitlines()
            if line.strip().startswith("http")
        ]

        if not image_urls:
            logger.warning("gallery-dl: no image URLs found for %s\nstderr: %s", url, proc.stderr[:300])
            return []

        logger.info("gallery-dl: found %d URL(s) for %s", len(image_urls), url)

        results = []
        for img_url in image_urls:
            try:
                r = requests.get(img_url, headers=_DL_HEADERS, timeout=30)
                if not r.ok:
                    logger.warning("gallery-dl: download %s → HTTP %s", img_url, r.status_code)
                    continue
                mime = r.headers.get("content-type", "image/jpeg").split(";", 1)[0].lower()
                if mime.startswith("image") or mime.startswith("video"):
                    results.append((r.content, mime))
            except requests.RequestException as e:
                logger.warning("gallery-dl: download error %s: %s", img_url, e)

        return results

    finally:
        for p in (cookies_path, config_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
