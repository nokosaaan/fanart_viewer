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


def fetch_twitter_media_gallerydl(url: str) -> tuple[list[tuple[bytes, str]], str]:
    """
    Fetch media from a tweet URL using gallery-dl.

    Returns ([(image_bytes, mime_type), ...], description_text).
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

        # -j / --dump-json (switched from --get-urls, which only ever
        # printed bare URLs — this app now also wants the tweet's own text,
        # see item.description). Doesn't download anything, same as
        # --get-urls did; verified against a real tweet. Output is a JSON
        # array of [type, data] entries: type 2 carries the tweet's own
        # metadata dict (content/hashtags/etc — shared across all its
        # media), type 3 carries a bare media URL string.
        proc = subprocess.run(
            [
                "gallery-dl",
                "--config", config_path,
                "-j",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if proc.returncode not in (0, 1):
            # returncode 1 = partial success (some URLs found but errors occurred)
            logger.warning("gallery-dl exited %s for %s: %s", proc.returncode, url, proc.stderr[:300])

        image_urls: list[str] = []
        description = ""
        try:
            entries = json.loads(proc.stdout or "[]")
        except ValueError:
            entries = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            entry_type, data = entry[0], entry[1]
            if entry_type == 3 and isinstance(data, str) and data.startswith("http"):
                image_urls.append(data)
            elif entry_type == 2 and isinstance(data, dict) and not description:
                description = data.get("content") or ""

        if not image_urls:
            logger.warning("gallery-dl: no image URLs found for %s\nstderr: %s", url, proc.stderr[:300])
            return [], description

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

        return results, description

    finally:
        for p in (cookies_path, config_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
