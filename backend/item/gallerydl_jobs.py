"""Background gallery-dl download jobs with incremental result delivery.

A multi-page gallery can contain hundreds of files and sites often throttle
requests (gallery-dl sleeps a few seconds between files), so a full download
can take many minutes. Instead of holding one HTTP request open until every
file is done, a job downloads in a background thread and the client polls for
files that have completed so far (`poll_job`), receiving them in batches.

No site-specific logic lives here: gallery-dl itself resolves the URL.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .gallerydl_fetch import (
    _GALLERYDL_CMD,
    _detect_mime_type,
    _natural_sort_key,
    _write_cookies,
    _write_generic_config,
)
from .image_optimizer import optimize_image_data
from .proxy_manager import acquire_proxy_session

logger = logging.getLogger(__name__)

# A job is killed if it produces neither a new file nor any output for this long.
IDLE_TIMEOUT_SECONDS = 180
# Finished jobs nobody collected (e.g. the browser was closed) are purged after this.
ABANDONED_TTL_SECONDS = 3600

_jobs: dict[str, "GalleryDlJob"] = {}
_jobs_lock = threading.Lock()


def _is_media_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    base = os.path.basename(path).lower()
    return not (base == "info.json" or base.endswith(".part") or base.endswith(".json"))


class GalleryDlJob:
    def __init__(self, url: str):
        self.id = uuid.uuid4().hex
        self.url = url
        self.created = time.time()
        self.finished_at: float | None = None
        self.status = "running"  # running | done | error | cancelled
        self.error = ""
        self.description = ""
        self.files: list[str] = []
        self.stderr_tail: list[str] = []
        self.lock = threading.Lock()
        self.tmpdir = tempfile.mkdtemp(prefix="gdl_job_")
        self.cookies_path: str | None = None
        self.config_path: str | None = None
        self.proc: subprocess.Popen | None = None
        self._seen: set[str] = set()
        self._real_tmpdir = os.path.realpath(self.tmpdir)
        self._cancel = False

    # -- file bookkeeping -------------------------------------------------
    def _add_file(self, path: str):
        cand = os.path.realpath(path)
        if not cand.startswith(self._real_tmpdir + os.sep) or not _is_media_file(cand):
            return
        with self.lock:
            if cand not in self._seen:
                self._seen.add(cand)
                self.files.append(cand)

    # -- worker -----------------------------------------------------------
    def run(self):
        activity = [time.monotonic()]
        try:
            try:
                from .twitter_creds import get_credentials as _tw
                auth_token, ct0 = _tw()
                if auth_token and ct0:
                    self.cookies_path = _write_cookies(auth_token, ct0)
            except Exception:
                self.cookies_path = None
            pixiv_phpsessid = None
            try:
                from .pixiv_creds import get_credentials as _px
                pixiv_phpsessid = (_px() or {}).get("phpsessid") or None
            except Exception:
                pixiv_phpsessid = None
            self.config_path = _write_generic_config(
                cookies_path=self.cookies_path, pixiv_phpsessid=pixiv_phpsessid,
            )

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            with acquire_proxy_session() as proxy_url:
                proxy_args = ["--proxy", proxy_url] if proxy_url else []
                if proxy_url:
                    logger.info("gallery-dl[%s]: running with proxy %s", self.id[:8], proxy_url)

                self.proc = subprocess.Popen(
                    [
                        *_GALLERYDL_CMD, "-v",
                        "--config", self.config_path,
                        *proxy_args,
                        "-D", self.tmpdir,
                        "--no-mtime", "--no-colors", "--write-info-json",
                        self.url,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", env=env,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}),
                )

                def pump_out():
                    for raw in iter(self.proc.stdout.readline, ""):
                        activity[0] = time.monotonic()
                        line = raw.strip()
                        if line.startswith("# "):
                            line = line[2:].strip()
                        if line:
                            self._add_file(line)

                def pump_err():
                    for raw in iter(self.proc.stderr.readline, ""):
                        activity[0] = time.monotonic()
                        text = raw.rstrip()
                        if text:
                            logger.info("gallery-dl[%s]: %s", self.id[:8], text[:400])
                            self.stderr_tail = (self.stderr_tail + [text])[-20:]

                threads = [threading.Thread(target=pump_out, daemon=True),
                           threading.Thread(target=pump_err, daemon=True)]
                for t in threads:
                    t.start()

                seen_count = 0
                timed_out = False
                while self.proc.poll() is None:
                    time.sleep(1.0)
                    with self.lock:
                        n = len(self.files)
                    if n != seen_count:
                        seen_count = n
                        activity[0] = time.monotonic()
                    if self._cancel:
                        self.proc.kill()
                        break
                    if time.monotonic() - activity[0] > IDLE_TIMEOUT_SECONDS:
                        timed_out = True
                        self.proc.kill()
                        break
                try:
                    self.proc.wait(timeout=10)
                except Exception:
                    pass
                for t in threads:
                    t.join(timeout=5)

            # Pick up anything gallery-dl wrote but did not print.
            try:
                for name in sorted(os.listdir(self.tmpdir), key=_natural_sort_key):
                    self._add_file(os.path.join(self.tmpdir, name))
            except OSError:
                pass

            info_path = os.path.join(self.tmpdir, "info.json")
            if os.path.isfile(info_path):
                try:
                    with open(info_path, "r", encoding="utf-8") as fp:
                        info = json.load(fp)
                    if isinstance(info, dict):
                        for key in ("content", "description", "title"):
                            val = info.get(key)
                            if isinstance(val, str) and val.strip():
                                self.description = val.strip()
                                break
                except Exception:
                    pass

            if self._cancel:
                self.status = "cancelled"
            elif not self.files:
                self.status = "error"
                tail = " / ".join(self.stderr_tail[-3:])
                self.error = ("進捗がないため中断しました" if timed_out else "画像を取得できませんでした") + (f": {tail}" if tail else "")
            else:
                self.status = "done"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("gallery-dl job %s crashed", self.id)
            self.status = "error"
            self.error = str(exc)
        finally:
            self.finished_at = time.time()
            for p in (self.cookies_path, self.config_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def purge(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _reap():
    now = time.time()
    with _jobs_lock:
        stale = [j for j in _jobs.values() if j.finished_at and now - j.finished_at > ABANDONED_TTL_SECONDS]
        for j in stale:
            _jobs.pop(j.id, None)
    for j in stale:
        j.purge()


def start_job(url: str) -> str:
    _reap()
    job = GalleryDlJob(url)
    with _jobs_lock:
        _jobs[job.id] = job
    threading.Thread(target=job.run, daemon=True, name=f"gdl-job-{job.id[:8]}").start()
    return job.id


def cancel_job(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return False
    job._cancel = True
    return True


def poll_job(job_id: str, since: int = 0, limit: int = 10):
    """Return the next batch of finished files, or None if the job is unknown.

    Result: dict(status, total_ready, next, images=[(bytes, mime)], description, error).
    Once the job is finished and every file has been delivered, its temp files
    are removed and the job is forgotten.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return None
    with job.lock:
        ready = list(job.files)
    batch_paths = ready[since:since + limit]
    images = []
    for path in batch_paths:
        try:
            with open(path, "rb") as fp:
                data = fp.read()
        except OSError:
            continue
        if not data:
            continue
        mime = _detect_mime_type(path, data)
        if (mime.startswith("image/") or mime.startswith("video/")) and mime != "image/svg+xml":
            if mime.startswith("image/"):
                data, mime = optimize_image_data(data, mime)
            images.append((data, mime))
        else:
            images.append((b"", ""))  # keep indexes aligned; caller skips empties
    next_index = since + len(batch_paths)
    finished = job.status != "running"
    drained = finished and next_index >= len(ready)
    result = {
        "status": job.status,
        "total_ready": len(ready),
        "next": next_index,
        "images": images,
        "description": job.description if finished else "",
        "error": job.error,
        "drained": drained,
    }
    if drained:
        with _jobs_lock:
            _jobs.pop(job_id, None)
        job.purge()
    return result


def encode_batch(images, base_url: str, start_index: int):
    """Convert poll_job images into the same JSON shape preview_only uses."""
    out = []
    for offset, (data, mime) in enumerate(images):
        if not data:
            continue
        n = start_index + offset + 1
        out.append({
            "index": n - 1,
            "url": f"{base_url}#img-{n}",
            "size": len(data),
            "content_type": mime,
            "data_uri": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
            "source": "gallerydl",
        })
    return out
