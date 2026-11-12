"""Secure persistent credential and encryption key storage.

Integrates with the operating system's native credential store:
  - Windows: Windows Credential Manager (資格情報マネージャー) via keyring or
    native ctypes (advapi32.dll: CredReadW / CredWriteW).
  - macOS: Keychain via keyring.
  - Linux: Secret Service / KWallet via keyring, with safe user-only file fallback.

Replaces plaintext `*_creds_key.txt` and `secret_key.txt` files in `~/.fanart_viewer/`,
preventing encryption keys and secrets from lingering on disk in plaintext while
migrating existing keys seamlessly to preserve database decryptability.
"""
import logging
import os
import secrets
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "fanart_viewer"

# Windows Credential Manager constants
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def _win_get_credential(target_name: str) -> str | None:
    """Read a generic credential from Windows Credential Manager via advapi32."""
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ('Flags', wintypes.DWORD),
                ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)),
                ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD),
                ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR),
                ('UserName', wintypes.LPWSTR),
            ]

        PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

        advapi32 = ctypes.windll.advapi32
        CredReadW = advapi32.CredReadW
        CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
        CredReadW.restype = wintypes.BOOL

        CredFree = advapi32.CredFree
        CredFree.argtypes = [wintypes.LPVOID]
        CredFree.restype = None

        p_cred = PCREDENTIALW()
        res = CredReadW(target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(p_cred))
        if not res:
            return None

        try:
            cred = p_cred.contents
            if cred.CredentialBlobSize > 0 and cred.CredentialBlob:
                raw_bytes = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
                return raw_bytes.decode('utf-8', errors='replace')
            return ""
        finally:
            CredFree(p_cred)
    except Exception as e:
        logger.debug("Windows Credential Manager read failed for %s: %s", target_name, e)
        return None


def _win_set_credential(target_name: str, secret: str, user_name: str = "fanart_viewer") -> bool:
    """Write a generic credential to Windows Credential Manager via advapi32."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ('Flags', wintypes.DWORD),
                ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)),
                ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD),
                ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR),
                ('UserName', wintypes.LPWSTR),
            ]

        PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

        advapi32 = ctypes.windll.advapi32
        CredWriteW = advapi32.CredWriteW
        CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
        CredWriteW.restype = wintypes.BOOL

        blob = secret.encode('utf-8')
        blob_len = len(blob)
        blob_buf = (ctypes.c_byte * blob_len).from_buffer_copy(blob)

        cred = CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target_name
        cred.Comment = "fanart_viewer persistent key"
        cred.CredentialBlobSize = blob_len
        cred.CredentialBlob = ctypes.cast(blob_buf, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = user_name

        res = CredWriteW(ctypes.byref(cred), 0)
        return bool(res)
    except Exception as e:
        logger.debug("Windows Credential Manager write failed for %s: %s", target_name, e)
        return False


def _fallback_dir() -> Path:
    custom = os.environ.get('FANART_VIEWER_DATA_DIR')
    if custom:
        base = Path(custom) / ".keys"
    else:
        try:
            base = Path.home() / ".fanart_viewer" / ".keys"
            base.mkdir(parents=True, exist_ok=True)
            if hasattr(os, 'chmod') and sys.platform != 'win32':
                os.chmod(base, 0o700)
            return base
        except Exception:
            import tempfile
            base = Path(tempfile.gettempdir()) / ".fanart_viewer_keys"

    base.mkdir(parents=True, exist_ok=True)
    try:
        if hasattr(os, 'chmod') and sys.platform != 'win32':
            os.chmod(base, 0o700)
    except Exception:
        pass
    return base


def _file_get_credential(key_name: str) -> str | None:
    f = _fallback_dir() / key_name
    if f.exists():
        try:
            return f.read_text(encoding='utf-8').strip()
        except Exception:
            return None
    return None


def _file_set_credential(key_name: str, secret: str) -> bool:
    f = _fallback_dir() / key_name
    try:
        f.write_text(secret.strip(), encoding='utf-8')
        if hasattr(os, 'chmod') and sys.platform != 'win32':
            os.chmod(f, 0o600)
        return True
    except Exception as e:
        logger.error("Failed to write fallback key file for %s: %s", key_name, e)
        return False


def get_credential(key_name: str, service_name: str = SERVICE_NAME) -> str | None:
    """Retrieve secret from OS credential store, falling back to secure local store."""
    # 1. Try keyring library if available
    try:
        import keyring
        val = keyring.get_password(service_name, key_name)
        if val is not None and val != "":
            return val
    except Exception:
        pass

    # 2. Try native Windows Credential Manager on Windows
    if sys.platform == 'win32':
        target = f"{service_name}:{key_name}"
        win_val = _win_get_credential(target)
        if win_val is not None and win_val != "":
            return win_val

    # 3. Fallback file
    return _file_get_credential(key_name)


def set_credential(key_name: str, secret: str, service_name: str = SERVICE_NAME) -> bool:
    """Store secret in OS credential store, falling back to secure local store."""
    stored = False

    # 1. Try keyring library
    try:
        import keyring
        keyring.set_password(service_name, key_name, secret)
        stored = True
    except Exception:
        pass

    # 2. Try native Windows Credential Manager on Windows
    if sys.platform == 'win32':
        target = f"{service_name}:{key_name}"
        if _win_set_credential(target, secret):
            stored = True

    # 3. Fallback file if neither OS backend succeeded
    if not stored:
        stored = _file_set_credential(key_name, secret)

    return stored


def get_or_create_persistent_key(
    key_name: str,
    legacy_file_path: Path | None = None,
    generator=None,
    service_name: str = SERVICE_NAME,
) -> str:
    """Fetch persistent key from OS credential store.

    If not found but `legacy_file_path` exists, seamlessly migrates the key
    into the OS credential store and removes the legacy plaintext file.
    If no key exists anywhere, generates a new one via `generator`.
    """
    existing = get_credential(key_name, service_name=service_name)
    if existing:
        # If legacy plaintext file is still around, delete it safely now that
        # the key is in the credential store.
        if legacy_file_path and legacy_file_path.exists():
            try:
                legacy_file_path.unlink()
                logger.info("Migrated key '%s' to OS credential store and deleted legacy file %s", key_name, legacy_file_path)
            except Exception as e:
                logger.warning("Could not delete legacy file %s after migration: %s", legacy_file_path, e)
        return existing

    # Not in credential store: check legacy file for existing key to preserve DB compatibility
    if legacy_file_path and legacy_file_path.exists():
        try:
            legacy_key = legacy_file_path.read_text(encoding='utf-8').strip()
            if legacy_key:
                set_credential(key_name, legacy_key, service_name=service_name)
                try:
                    legacy_file_path.unlink()
                    logger.info("Migrated key '%s' from %s to OS credential store", key_name, legacy_file_path)
                except Exception as e:
                    logger.warning("Could not delete legacy file %s after migration: %s", legacy_file_path, e)
                return legacy_key
        except Exception as e:
            logger.error("Failed reading legacy key from %s: %s", legacy_file_path, e)

    # Generate fresh key
    if generator is None:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key().decode()
    else:
        new_key = generator()

    set_credential(key_name, new_key, service_name=service_name)
    return new_key
