"""Same shape/reasoning as backup_progress.py, for drive_backup.restore_backup
-- see that module's docstring for the "why background instead of blocking
the request" design this mirrors.

Restore has one extra wrinkle backup doesn't: its own existing-data safety
check (ExistingDataError, asking the user to confirm overwrite/merge
before touching anything) needs the backup file already downloaded to
compare row counts against what's already in the local DB -- meaning that
confirmation step ALSO used to block synchronously behind the file's full
download, the exact same "no idea how far along" problem create_backup
had, just one step earlier. This backgrounds that check too, not only the
restore proper: `needs_confirmation` is a terminal state alongside
`result`/`error`, not a separate synchronous request/response.
"""
import logging
import threading
import time

from . import drive_backup
from .drive_backup import DriveBackupError, ExistingDataError

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    'running': False,
    'phase': None,        # 'starting' | 'downloading' | 'importing' | None
    'percent': None,       # 0-100, or None while indeterminate (the DB-side import itself)
    'started_at': None,
    'finished_at': None,
    'error': None,
    # {'current': {...}, 'backup': {...}} row counts once a strict-mode
    # restore finds existing data and needs the user to pick overwrite/
    # merge -- None otherwise. Nothing was touched when this is set.
    'needs_confirmation': None,
    'result': None,        # {} for strict/overwrite, or the merge summary dict for merge mode
}


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _on_progress(phase, percent):
    with _lock:
        _state['phase'] = phase
        _state['percent'] = percent


def _run(file_id, mode):
    try:
        result = drive_backup.restore_backup(file_id, mode=mode, progress_cb=_on_progress)
        with _lock:
            _state.update(
                running=False, finished_at=time.time(), percent=100,
                result=result or {}, error=None, needs_confirmation=None,
            )
    except ExistingDataError as e:
        with _lock:
            _state.update(
                running=False, finished_at=time.time(),
                needs_confirmation={'current': e.current, 'backup': e.backup}, error=None,
            )
    except DriveBackupError as e:
        with _lock:
            _state.update(running=False, finished_at=time.time(), error=str(e))
    except Exception as e:
        logger.exception('Restore failed')
        with _lock:
            _state.update(running=False, finished_at=time.time(), error=str(e))


def start(file_id: str, mode: str = 'strict'):
    """Raises RuntimeError if a restore is already running — never two at
    once (they'd both be writing to the same DB)."""
    with _lock:
        if _state['running']:
            raise RuntimeError('既に復元が実行中です')
        _state.update({
            'running': True, 'phase': 'starting', 'percent': 0,
            'started_at': time.time(), 'finished_at': None, 'error': None,
            'needs_confirmation': None, 'result': None,
        })
    threading.Thread(target=_run, args=(file_id, mode), daemon=True).start()
