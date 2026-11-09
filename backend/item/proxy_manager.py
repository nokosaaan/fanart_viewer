"""Proxy and Tor lifecycle manager for gallery-dl and HTTP fetch operations.

Supports:
1. 'off': No proxy.
2. 'tor_auto': Automatically launches a lightweight background `tor` process
   on demand when downloads start, keeps it alive while active, and terminates
   it after an idle timeout once all active downloads finish.
3. 'custom': Uses a user-specified proxy URL (e.g. Tor Browser 'socks5h://127.0.0.1:9150'
   or Cloudflare WARP 'socks5h://127.0.0.1:40000').
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Base directory for storing configuration file
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CONFIG_FILE = _DATA_DIR / "proxy_config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "mode": "tor_auto",  # 'tor_auto' | 'custom'
    "tor_path": "",
    "tor_port": 9052,
    "custom_proxy_url": "socks5h://127.0.0.1:9150",
    "idle_timeout_seconds": 60,
}


def load_config() -> dict[str, Any]:
    """Load proxy configuration from file, falling back to defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        if _CONFIG_FILE.exists():
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
    except Exception as e:
        logger.warning("Failed to load proxy config: %s", e)
    return cfg


def save_config(new_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist proxy configuration."""
    cfg = load_config()
    for k in ("enabled", "mode", "tor_path", "tor_port", "custom_proxy_url", "idle_timeout_seconds"):
        if k in new_cfg:
            cfg[k] = new_cfg[k]

    # Normalize port
    try:
        cfg["tor_port"] = int(cfg.get("tor_port", 9052))
    except (ValueError, TypeError):
        cfg["tor_port"] = 9052

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


def resolve_tor_binary(custom_path: str = "") -> str | None:
    """Find the path to the `tor` executable.

    Search priority:
    1. custom_path (if configured and exists)
    2. PyInstaller bundled path (_internal/tor/tor.exe, tor/tor.exe, or MEIPASS/tor/tor.exe)
    3. Development repo path (exe/tor_win/tor/tor.exe)
    4. System PATH (`tor.exe` or `tor`)
    """
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    # Check PyInstaller frozen environment
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates = [
            exe_dir / "tor" / "tor.exe",
            exe_dir / "_internal" / "tor" / "tor.exe",
            exe_dir / "tor" / "tor",
            exe_dir / "_internal" / "tor" / "tor",
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.extend([
                Path(meipass) / "tor" / "tor.exe",
                Path(meipass) / "tor" / "tor",
            ])
        for cand in candidates:
            if cand.is_file():
                return str(cand)

    # Check development repo path (Windows bundle under exe/tor_win/)
    dev_tor_exe = Path(__file__).resolve().parent.parent.parent / "exe" / "tor_win" / "tor" / "tor.exe"
    if dev_tor_exe.is_file():
        return str(dev_tor_exe)

    # Check system PATH
    return shutil.which("tor.exe") or shutil.which("tor")


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def _get_tor_data_dir() -> Path:
    """Return a persistent data directory for Tor consensus and guard cache."""
    base_dir = Path.home() / ".fanart_viewer"
    if not base_dir.exists():
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            base_dir = _DATA_DIR
    d = base_dir / "tor_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TorProcessManager:
    """Thread-safe manager for an on-demand background Tor process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._data_dir: Path | None = None
        self._ref_count = 0
        self._idle_timer: threading.Timer | None = None
        self._current_port = 9052
        self._bootstrapped_event = threading.Event()
        self._bootstrap_status = ""

    def is_running(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            return is_port_open("127.0.0.1", self._current_port)

    def acquire(self, tor_path: str = "", port: int = 9052, idle_timeout: int = 60) -> str:
        """Increment reference count and ensure Tor is running on `port`.

        Returns the SOCKS proxy URL.
        """
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

            self._current_port = port
            self._ref_count += 1

            # If port is already responding (either our process or existing daemon), reuse it
            if is_port_open("127.0.0.1", port):
                logger.info("Tor port %d is already open, reusing existing instance", port)
                return f"socks5h://127.0.0.1:{port}"

            # If our process died unexpectedly, clean up
            if self._proc is not None and self._proc.poll() is not None:
                self._cleanup_process_locked()

            # Launch Tor subprocess
            binary = resolve_tor_binary(tor_path)
            if not binary:
                self._ref_count -= 1
                raise RuntimeError(
                    "Tor の実行ファイルが見つかりません。システムに tor をインストールするか、パスを指定してください。"
                )

            self._data_dir = _get_tor_data_dir()
            self._bootstrapped_event.clear()
            self._bootstrap_status = "起動中..."

            # Look for optional geoip files located alongside tor binary
            geoip_file = None
            geoip6_file = None
            bin_path = Path(binary)
            for cand_dir in [bin_path.parent / "data", bin_path.parent.parent / "data", bin_path.parent]:
                g = cand_dir / "geoip"
                g6 = cand_dir / "geoip6"
                if g.is_file():
                    geoip_file = str(g)
                if g6.is_file():
                    geoip6_file = str(g6)
                if geoip_file:
                    break

            cmd = [
                binary,
                "--SocksPort", str(port),
                "--DataDirectory", str(self._data_dir),
                "--PidFile", str(self._data_dir / "tor.pid"),
                "--Log", "notice stdout",
            ]
            if geoip_file:
                cmd.extend(["--GeoIPFile", geoip_file])
            if geoip6_file:
                cmd.extend(["--GeoIPv6File", geoip6_file])

            logger.info("Launching Tor process on port %d: %s", port, " ".join(cmd))
            env = os.environ.copy()
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}),
            )

            # Background thread to continuously read Tor stdout to prevent pipe deadlocks
            # and detect when circuit bootstrapping completes (100% Done).
            def _pump_tor_stdout(proc: subprocess.Popen, event: threading.Event):
                try:
                    for raw in iter(proc.stdout.readline, ""):
                        line = raw.strip()
                        if not line:
                            continue
                        if (
                            "Bootstrapped" in line
                            or "Opening Socks" in line
                            or "Opened Socks" in line
                            or "warn" in line.lower()
                        ):
                            logger.info("Tor: %s", line)
                        if "Bootstrapped" in line:
                            self._bootstrap_status = line
                        if ("Bootstrapped 100%" in line and "Done" in line) or "100%: Done" in line:
                            event.set()
                except Exception:
                    pass

            pump_thread = threading.Thread(
                target=_pump_tor_stdout,
                args=(self._proc, self._bootstrapped_event),
                daemon=True,
            )
            pump_thread.start()

            # Wait for SOCKS listener port to open (up to 15 seconds)
            ready = False
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    self._cleanup_process_locked()
                    self._ref_count -= 1
                    raise RuntimeError("Tor の起動に失敗しました (プロセスが早期終了しました)")
                if is_port_open("127.0.0.1", port):
                    ready = True
                    break
                time.sleep(0.5)

            if not ready:
                self.stop_locked()
                self._ref_count -= 1
                raise RuntimeError("Tor の起動待受タイムアウト (15秒) を超過しました")

            # Wait for Tor circuit bootstrapping to finish (up to 45s on cold start, 1-3s on warm start)
            bootstrapped = self._bootstrapped_event.wait(timeout=45.0)
            if not bootstrapped:
                logger.warning(
                    "Tor bootstrap did not complete within 45s (current status: %s), proceeding anyway",
                    self._bootstrap_status,
                )

            logger.info("Tor successfully started and listening on 127.0.0.1:%d", port)
            return f"socks5h://127.0.0.1:{port}"

    def release(self, idle_timeout: int = 60):
        """Decrement reference count; stop process after idle_timeout if ref count is 0."""
        with self._lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count == 0 and self._proc is not None:
                if self._idle_timer is not None:
                    self._idle_timer.cancel()
                self._idle_timer = threading.Timer(idle_timeout, self._on_idle_timeout)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _on_idle_timeout(self):
        with self._lock:
            if self._ref_count == 0 and self._proc is not None:
                logger.info("Idle timeout reached with 0 active downloads; stopping Tor process")
                self.stop_locked()

    def stop_locked(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        self._cleanup_process_locked()
        self._ref_count = 0

    def stop(self):
        with self._lock:
            self.stop_locked()

    def _cleanup_process_locked(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            self._bootstrapped_event.clear()


_tor_manager = TorProcessManager()


@contextmanager
def acquire_proxy_session():
    """Context manager that acquires and releases proxy resources for a download operation.

    Yields the proxy URL string (e.g. 'socks5h://127.0.0.1:9052') or None if disabled.
    """
    cfg = load_config()
    if not cfg.get("enabled"):
        yield None
        return

    mode = cfg.get("mode", "tor_auto")
    if mode == "custom":
        url = (cfg.get("custom_proxy_url") or "").strip()
        yield url or None
        return

    if mode == "tor_auto":
        port = int(cfg.get("tor_port", 9052))
        idle_timeout = int(cfg.get("idle_timeout_seconds", 60))
        tor_path = (cfg.get("tor_path") or "").strip()
        proxy_url = _tor_manager.acquire(tor_path=tor_path, port=port, idle_timeout=idle_timeout)
        try:
            yield proxy_url
        finally:
            _tor_manager.release(idle_timeout=idle_timeout)
        return

    yield None


def get_current_proxy_status() -> dict[str, Any]:
    """Return current configuration, Tor availability, and running status."""
    cfg = load_config()
    tor_binary = resolve_tor_binary(cfg.get("tor_path", ""))
    tor_port = int(cfg.get("tor_port", 9052))
    port_open = is_port_open("127.0.0.1", tor_port)

    return {
        "config": cfg,
        "tor_installed": bool(tor_binary),
        "tor_binary_path": tor_binary or "",
        "tor_port_open": port_open,
        "tor_process_active": _tor_manager.is_running(),
    }


def test_proxy_connection(proxy_url: str) -> dict[str, Any]:
    """Test connection through the given proxy URL and return result details."""
    import requests

    if not proxy_url:
        return {"success": False, "error": "プロキシURLが指定されていません"}

    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }
    t0 = time.monotonic()

    # 1. Tor project's official API (specifically designed for Tor checking, no rate limits)
    try:
        resp = requests.get(
            "https://check.torproject.org/api/ip",
            proxies=proxies,
            timeout=15,
            headers={"User-Agent": "fanart_viewer/1.0"},
        )
        elapsed = round(time.monotonic() - t0, 2)
        if resp.ok:
            data = resp.json()
            ip = data.get("IP") or data.get("ip") or "不明"
            return {
                "success": True,
                "ip": ip,
                "is_tor": bool(data.get("IsTor", False)),
                "elapsed_seconds": elapsed,
                "proxy_used": proxy_url,
            }
    except Exception as e:
        logger.info("check.torproject.org check failed: %s, trying fallback", e)

    # 2. Cloudflare-backed icanhazip (pure plaintext IP, highly reliable)
    try:
        resp = requests.get(
            "https://icanhazip.com",
            proxies=proxies,
            timeout=15,
            headers={"User-Agent": "curl/7.68.0"},
        )
        elapsed = round(time.monotonic() - t0, 2)
        if resp.ok and resp.text.strip():
            return {
                "success": True,
                "ip": resp.text.strip(),
                "is_tor": False,
                "elapsed_seconds": elapsed,
                "proxy_used": proxy_url,
            }
    except Exception as e:
        logger.info("icanhazip check failed: %s, trying fallback", e)

    # 3. httpbin fallback
    try:
        resp = requests.get(
            "https://httpbin.org/ip",
            proxies=proxies,
            timeout=15,
        )
        elapsed = round(time.monotonic() - t0, 2)
        if resp.ok:
            data = resp.json()
            ip = data.get("origin", "不明").split(",")[0].strip()
            return {
                "success": True,
                "ip": ip,
                "is_tor": False,
                "elapsed_seconds": elapsed,
                "proxy_used": proxy_url,
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"プロキシ経由での通信テストに失敗しました: {e}",
            "proxy_used": proxy_url,
        }

    return {
        "success": False,
        "error": "すべての接続確認エンドポイントへの通信に失敗しました",
        "proxy_used": proxy_url,
    }
