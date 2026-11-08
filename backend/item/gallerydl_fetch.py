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
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests
from .proxy_manager import acquire_proxy_session

# The exe-packaged build has no separate `gallery-dl` executable on PATH
# (pip's normal console-script wrapper never gets bundled by PyInstaller --
# only gallery_dl the PYTHON PACKAGE does) -- exe/launcher.py handles this
# with its own --run-gallery-dl mode, re-invoking this same frozen exe as a
# subprocess instead of a nonexistent `gallery-dl` binary. `sys.frozen` is
# the standard PyInstaller idiom for "am I running from a frozen build".
_GALLERYDL_CMD = (
    [sys.executable, '--run-gallery-dl']
    if getattr(sys, 'frozen', False)
    else (['gallery-dl'] if shutil.which('gallery-dl') else [sys.executable, '-m', 'gallery_dl'])
)

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
        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        with acquire_proxy_session() as proxy_url:
            proxy_args = ["--proxy", proxy_url] if proxy_url else []
            proc = subprocess.run(
                [
                    *_GALLERYDL_CMD,
                    "--config", config_path,
                    *proxy_args,
                    "-j",
                    url,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                timeout=60,
                # In the frozen exe, this launches the exe itself again (see
                # _GALLERYDL_CMD) -- without this, Windows briefly flashes a
                # console window for that child process on every single
                # fetch, which reads as broken/janky for a windowed app that
                # otherwise never shows one. CREATE_NO_WINDOW only exists on
                # Windows; harmless no-op via getattr elsewhere.
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}),
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
            req_proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            for img_url in image_urls:
                try:
                    r = requests.get(img_url, headers=_DL_HEADERS, timeout=30, proxies=req_proxies)
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


def _natural_sort_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name or "")]


def _detect_mime_type(path: str, data: bytes) -> str:
    """Detect media MIME type from magic bytes with filename extension fallback."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    guessed, _ = mimetypes.guess_type(path)
    return (guessed or "application/octet-stream").lower()


def _write_generic_config(cookies_path: str | None = None, pixiv_phpsessid: str | None = None) -> str:
    """Write a gallery-dl config file for general web gallery fetching."""
    extractor_cfg: dict = {
        "skip": "enumerate",
        "generic": {
            "enabled": True,
        },
    }
    if cookies_path:
        extractor_cfg["twitter"] = {
            "cookies": cookies_path,
            "sensitive": True,
            "size": "orig",
        }
    if pixiv_phpsessid:
        extractor_cfg["pixiv"] = {
            "cookies": {"PHPSESSID": pixiv_phpsessid},
        }
    cfg = {"extractor": extractor_cfg}
    fd, path = tempfile.mkstemp(suffix=".json", prefix="gdl_config_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def fetch_media_gallerydl(url: str, timeout: int = 120) -> tuple[list[tuple[bytes, str]], str]:
    """Fetch media from a web page or multi-page gallery URL using gallery-dl.

    Unlike `fetch_twitter_media_gallerydl` (which dumps bare URLs via `-j`),
    this lets gallery-dl perform the actual file downloads into a temporary
    directory so multi-page pagination, intermediary viewer pages, session
    headers/cookies, and fallback mirrors are all handled by gallery-dl's own
    extractor pipeline.

    Returns ([(image_bytes, mime_type), ...], description_text).
    """
    cookies_path = config_path = tmpdir = None
    try:
        try:
            from .twitter_creds import get_credentials as _get_twitter_creds
            auth_token, ct0 = _get_twitter_creds()
            if auth_token and ct0:
                cookies_path = _write_cookies(auth_token, ct0)
        except Exception:
            cookies_path = None

        pixiv_phpsessid = None
        try:
            from .pixiv_creds import get_credentials as _get_pixiv_creds
            pixiv_phpsessid = (_get_pixiv_creds() or {}).get("phpsessid") or None
        except Exception:
            pixiv_phpsessid = None

        config_path = _write_generic_config(cookies_path=cookies_path, pixiv_phpsessid=pixiv_phpsessid)
        tmpdir = tempfile.mkdtemp(prefix="gdl_dl_")

        stdout_text = ""
        stderr_text = ""
        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        with acquire_proxy_session() as proxy_url:
            proxy_args = ["--proxy", proxy_url] if proxy_url else []
            if proxy_url:
                logger.info("gallery-dl: running with proxy %s", proxy_url)

            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            last_activity = [time.monotonic()]

            def _pump(stream, sink, is_err):
                try:
                    for raw in iter(stream.readline, ""):
                        sink.append(raw)
                        last_activity[0] = time.monotonic()
                        if is_err and raw.strip():
                            logger.info("gallery-dl: %s", raw.rstrip()[:400])
                except Exception:
                    pass

            proc = subprocess.Popen(
                [
                    *_GALLERYDL_CMD,
                    "-v",
                    "--config", config_path,
                    *proxy_args,
                    "-D", tmpdir,
                    "--no-mtime",
                    "--no-colors",
                    "--write-info-json",
                    url,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}),
            )
            pumps = [
                threading.Thread(target=_pump, args=(proc.stdout, stdout_chunks, False), daemon=True),
                threading.Thread(target=_pump, args=(proc.stderr, stderr_chunks, True), daemon=True),
            ]
            for t in pumps:
                t.start()

            seen_files = 0
            timed_out = False
            while proc.poll() is None:
                time.sleep(1.0)
                try:
                    n = len(os.listdir(tmpdir))
                except OSError:
                    n = seen_files
                if n != seen_files:
                    seen_files = n
                    last_activity[0] = time.monotonic()
                if time.monotonic() - last_activity[0] > timeout:
                    timed_out = True
                    proc.kill()
                    break
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            for t in pumps:
                t.join(timeout=5)

            stdout_text = "".join(stdout_chunks)
            stderr_text = "".join(stderr_chunks)
        if timed_out:
            logger.warning(
                "gallery-dl made no progress for %ss on %s (files so far: %d); collecting what was downloaded",
                timeout, url, seen_files,
            )
        elif proc.returncode not in (0, 1):
            logger.warning("gallery-dl exited %s for %s: %s", proc.returncode, url, stderr_text[-300:])

        description = ""
        info_path = os.path.join(tmpdir, "info.json")
        if os.path.isfile(info_path):
            try:
                with open(info_path, "r", encoding="utf-8") as fp:
                    info = json.load(fp)
                if isinstance(info, dict):
                    for key in ("content", "description", "title"):
                        val = info.get(key)
                        if isinstance(val, str) and val.strip():
                            description = val.strip()
                            break
            except Exception:
                pass

        def _is_media_file(p: str) -> bool:
            if not os.path.isfile(p):
                return False
            base = os.path.basename(p).lower()
            if base == "info.json" or base.endswith(".part") or base.endswith(".json"):
                return False
            return True

        ordered_paths: list[str] = []
        seen_paths: set[str] = set()
        real_tmpdir = os.path.realpath(tmpdir)

        for raw_line in stdout_text.splitlines():
            line = raw_line.strip()
            if line.startswith("# "):
                line = line[2:].strip()
            if not line:
                continue
            cand = os.path.realpath(line)
            if cand.startswith(real_tmpdir + os.sep) and _is_media_file(cand) and cand not in seen_paths:
                ordered_paths.append(cand)
                seen_paths.add(cand)

        remaining_names = sorted(os.listdir(tmpdir), key=_natural_sort_key)
        for name in remaining_names:
            cand = os.path.realpath(os.path.join(tmpdir, name))
            if _is_media_file(cand) and cand not in seen_paths:
                ordered_paths.append(cand)
                seen_paths.add(cand)

        results: list[tuple[bytes, str]] = []
        for fpath in ordered_paths:
            try:
                with open(fpath, "rb") as fp:
                    data = fp.read()
                if not data:
                    continue
                mime = _detect_mime_type(fpath, data)
                if (mime.startswith("image/") or mime.startswith("video/")) and mime != "image/svg+xml":
                    results.append((data, mime))
            except OSError as e:
                logger.warning("gallery-dl: failed to read downloaded file %s: %s", fpath, e)

        if not results:
            logger.warning("gallery-dl: no media downloaded for %s\nstderr: %s", url, stderr_text[:300])
        else:
            logger.info("gallery-dl: downloaded %d file(s) for %s", len(results), url)

        return results, description

    finally:
        for p in (cookies_path, config_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
