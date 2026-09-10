"""Ensures Playwright's Chromium browser binary is present before launch.

The exe-packaged distribution (see exe/launcher.py) bundles the Playwright
Python package and its driver (the Node-based engine that both runs
browsers and implements `playwright install` itself — needed either way,
so it ships in the build), but deliberately does NOT bundle the Chromium
browser binary itself (~280MB). launcher.py points PLAYWRIGHT_BROWSERS_PATH
at a persistent per-user directory, and this module downloads Chromium
there on first actual use, exactly like item/tagger.py's own on-demand
model download.

Calling `playwright install chromium` (the driver's own install command,
invoked the same way playwright.__main__ does it — see
playwright._impl._driver) is itself idempotent/fast when Chromium is
already present, so no separate "is it installed" check is needed here.
"""
import subprocess
import threading

_lock = threading.Lock()
_installed = False


def ensure_chromium_installed(timeout_sec=600):
    """Download Chromium if missing. Raises RuntimeError with the driver's
    own error output on failure (e.g. no network on first run)."""
    global _installed
    if _installed:
        return
    with _lock:
        if _installed:
            return
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        driver_executable, driver_cli = compute_driver_executable()
        result = subprocess.run(
            [driver_executable, driver_cli, 'install', 'chromium'],
            env=get_driver_env(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            # Avoid a briefly-flashing console window on Windows -- this
            # runs from a background thread of a windowed app that
            # otherwise never shows one (see the identical fix/comment in
            # item/gallerydl_fetch.py). Windows-only flag; no-op elsewhere.
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}),
        )
        if result.returncode != 0:
            raise RuntimeError(
                'Chromium のダウンロードに失敗しました: '
                + (result.stderr or result.stdout).strip()[-2000:]
            )
        _installed = True
