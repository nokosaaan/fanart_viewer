"""Lets the packaged exe's "分類器の学習" GUI panel (TrainClassifierManager.jsx)
kick off item.management.commands.train_character_classifier without the
user ever touching PowerShell/train.ps1 — the user who filed this literally
couldn't get train.ps1 to run at all (execution-policy/file-association
trouble is common for a non-technical Windows user who "just double-clicks
things"), and separately asked for GUI parameter control instead of
remembering CLI flags.

Deliberately still runs as a SEPARATE subprocess (`sys.executable
--train-classifier ...`, exactly what exe/train.ps1 itself does via
Start-Process) rather than importing and calling the management command
in-process on a background thread. Reasons this matters:
  - The already-running app may have the tagger/classifier already loaded
    in memory (see tagger.py's module-level cache) — training re-loads and
    re-fits fresh state of its own; keeping that entirely in a separate
    process avoids any chance of the two stepping on each other's cached
    state or fighting over the same GIL for a CPU-bound job that can run
    for hours.
  - training.log tailing (see training_log_path/read_log_tail) already
    exists and works (exe/launcher.py's IS_TRAINING_MODE branch), so this
    reuses it instead of building a second, separate log-capture mechanism
    for the in-process case.

Only meaningful for the frozen (PyInstaller) exe build — `sys.executable`
in a normal `python manage.py runserver` dev/Docker process is the Python
interpreter, not this app, so `--train-classifier` would mean nothing to
it (that flag is only ever inspected by exe/launcher.py, which dev/Docker
never runs at all). is_available() gates the whole feature on this.
"""
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from item import tagger

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    'running': False,
    'args': [],
    'started_at': None,
    'finished_at': None,
    'returncode': None,
    'start_error': None,
}


def is_available() -> bool:
    """Only the frozen exe can relaunch itself with --train-classifier —
    see module docstring."""
    return bool(getattr(sys, 'frozen', False))


def training_log_path() -> Path:
    """Same path exe/launcher.py's IS_TRAINING_MODE branch already writes
    to (see USER_DATA_DIR/training.log there) — read, never written to,
    from this module; the subprocess itself owns writing it."""
    return Path.home() / '.fanart_viewer' / 'training.log'


# Options a regular user might actually want to change — see exe/README.txt,
# which documents these same as "usually all you need" (min-images and
# backend/classifier defaults are already the recommended values; --exclude
# is the one RELEASE_LOCAL.md flags as commonly necessary in practice).
# `update_cache` (a checkbox, not a raw path — see below) is the one
# exception to "power-user tuning knob": re-running the tagger on every
# already-processed image every single time is expensive enough on a
# personal machine that skipping it by default for anyone who trains more
# than once is worth the one extra checkbox.
# Deliberately still NOT exposed here: --feature-cache/--use-cache (the
# frozen, no-DB-check variant)/--classifier/--test-size/--random-state/
# --output/--max-images-per-character/--feature-source — all real argparse
# options, just power-user tuning knobs irrelevant to "I added some new
# characters, let's retrain" (still reachable via train.ps1's own
# passthrough args for anyone who wants them).
def _build_argv(options: dict) -> list:
    argv = []

    backend_choice = options.get('backend')
    if backend_choice not in ('onnx', 'canary'):
        backend_choice = 'onnx'  # train_character_classifier's own default
    argv += ['--backend', backend_choice]

    min_images = options.get('min_images')
    if min_images is not None:
        try:
            argv += ['--min-images', str(int(min_images))]
        except (TypeError, ValueError):
            raise ValueError('min_images must be an integer')

    exclude = options.get('exclude')
    if isinstance(exclude, list):
        names = [str(n).strip() for n in exclude if str(n).strip()]
        if names:
            argv += ['--exclude', ','.join(names)]
    elif isinstance(exclude, str) and exclude.strip():
        argv += ['--exclude', exclude.strip()]

    if options.get('include_multi_character'):
        argv += ['--include-multi-character']

    # A checkbox, not a free-text path field — a non-technical user has no
    # reason to ever pick where this file lives, and always pointing at the
    # SAME well-known default (matching train_character_classifier.py's own
    # --feature-cache default naming exactly, keyed only by --backend) is
    # what actually lets this be reused run after run: --feature-source
    # isn't exposed in this GUI at all, so it's always the 'tags' variant
    # of that default name.
    if options.get('update_cache'):
        cache_path = os.path.join(tagger._data_dir(), f'character_features_{backend_choice}.joblib')
        argv += ['--update-cache', cache_path]

    return argv


def _watch(proc):
    """Runs on a daemon thread for the lifetime of the subprocess — the
    only way to learn it exited (and with what code) without the frontend
    having to somehow detect that itself, which it structurally can't:
    a closed browser tab/panel doesn't stop the subprocess (see module
    docstring), so this is the sole source of truth for "did it finish"."""
    returncode = proc.wait()
    with _lock:
        _state['running'] = False
        _state['finished_at'] = time.time()
        _state['returncode'] = returncode
    logger.info('train_character_classifier subprocess exited with code %s', returncode)


def start(options: dict):
    """Raises ValueError (bad options) or RuntimeError (already running /
    not available / failed to launch) — never starts a second concurrent
    run, since two simultaneous fits would both be writing the same
    character_classifier_<backend>.joblib output path."""
    if not is_available():
        raise RuntimeError('この機能はビルド済みexe版でのみ利用できます')
    with _lock:
        if _state['running']:
            raise RuntimeError('既に学習が実行中です')
        argv = _build_argv(options)

        log_path = training_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Reset per-run, exactly like exe/train.ps1's own
        # Remove-Item $logPath before Start-Process — otherwise the tail
        # read below would show a previous run's leftover output mixed in
        # with this one's.
        try:
            log_path.unlink()
        except FileNotFoundError:
            pass

        try:
            proc = subprocess.Popen([sys.executable, '--train-classifier', *argv])
        except Exception as e:
            logger.exception('Failed to start train_character_classifier subprocess')
            raise RuntimeError(f'学習プロセスの起動に失敗しました: {e}')

        _state['running'] = True
        _state['args'] = argv
        _state['started_at'] = time.time()
        _state['finished_at'] = None
        _state['returncode'] = None
        _state['start_error'] = None

        threading.Thread(target=_watch, args=(proc,), daemon=True).start()


# Cap how much of training.log a single status poll reads back — this can
# grow to many MB over a long run (every image logged during feature
# extraction), and the frontend only ever shows a scrolling tail anyway, so
# there is no reason to ship megabytes over the wire on every ~2s poll.
_LOG_TAIL_BYTES = 20_000


def _read_log_tail() -> str:
    path = training_log_path()
    try:
        size = path.stat().st_size
        with path.open('rb') as f:
            if size > _LOG_TAIL_BYTES:
                f.seek(size - _LOG_TAIL_BYTES)
            data = f.read()
        text = data.decode('utf-8', errors='replace')
        if size > _LOG_TAIL_BYTES:
            text = '...(先頭省略)...\n' + text.split('\n', 1)[-1]
        return text
    except FileNotFoundError:
        return ''


def get_status() -> dict:
    with _lock:
        state = dict(_state)
    state['available'] = is_available()
    state['log_tail'] = _read_log_tail()
    return state
