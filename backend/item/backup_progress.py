"""Turns drive_backup.create_backup() into a backgroundable, progress-
reporting operation for BackupManager.jsx — it used to run synchronously
inside the HTTP request/response cycle with nothing to show but a static
"バックアップ中…" spinner for however long the whole thing took (RELEASE_
LOCAL.md already warns a big SQLite/Postgres DB can make even a Cloudflare-
Tunnelled request run long enough to hit that tunnel's own ~100s timeout —
the exe's own local loopback request has no tunnel in the way, but the
"nothing to show for a long wait" problem is the same either way).

Mirrors item/classifier_training.py's shape (lock-protected state dict,
started on a daemon thread, polled via a status view) rather than that
module's own mechanism (a separate subprocess) — a Drive backup only ever
touches in-process Python APIs (sqlite3, google-api-python-client, or a
pg_dump subprocess this module doesn't need to isolate itself from), so
there's nothing here that benefits from the extra process boundary a
multi-hour classifier fit does.
"""
import logging
import threading
import time

from . import drive_backup

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    'running': False,
    'phase': None,       # 'sqlite_backup' | 'dumping' | 'compressing' | 'uploading' | None
    'percent': None,      # 0-100, or None while indeterminate (e.g. pg_dump itself)
    'started_at': None,
    'finished_at': None,
    'error': None,
    'result': None,       # the created Drive file's metadata, on success
}


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _on_progress(phase, percent):
    with _lock:
        _state['phase'] = phase
        _state['percent'] = percent


def _run():
    try:
        result = drive_backup.create_backup(progress_cb=_on_progress)
        with _lock:
            _state['running'] = False
            _state['finished_at'] = time.time()
            _state['result'] = result
            _state['percent'] = 100
    except Exception as e:
        logger.exception('Backup failed')
        with _lock:
            _state['running'] = False
            _state['finished_at'] = time.time()
            _state['error'] = str(e)


def start():
    """Raises RuntimeError if a backup is already running — never runs two
    concurrently (they'd both be reading a live-changing DB independently
    and racing to create separate Drive files for no reason).

    Also refuses to start while a restore is running (see restore_progress.py)
    -- restore can delete/overwrite the very tables a backup would be
    reading mid-dump, and a backup started right as a restore finishes
    would capture a half-imported DB. This is the only cross-check between
    the two modules; restore_progress.start() has the mirror-image check.
    """
    from . import restore_progress

    if restore_progress.get_status()['running']:
        raise RuntimeError('復元処理が進行中のため、完了するまでバックアップを開始できません')
    with _lock:
        if _state['running']:
            raise RuntimeError('既にバックアップが実行中です')
        _state.update({
            'running': True, 'phase': 'starting', 'percent': 0,
            'started_at': time.time(), 'finished_at': None, 'error': None, 'result': None,
        })
    threading.Thread(target=_run, daemon=True).start()
