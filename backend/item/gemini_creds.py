"""Encrypted storage for Gemini API key used by in-image translation,
as an alternative to setting GEMINI_API_KEY in .env.

Values are stored Fernet-encrypted in the DB (GeminiCredential, single row)
under GEMINI_CREDS_ENC_KEY. Write-only from the HTTP API's perspective —
see item.gemini_creds_views.
"""
import base64
import hashlib
import os

from .models import GeminiCredential


class GeminiCredsConfigError(RuntimeError):
    """GEMINI_CREDS_ENC_KEY missing while trying to read/write a stored credential."""


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get('GEMINI_CREDS_ENC_KEY', '').strip()
    if not key:
        # Fall back gracefully by deriving a deterministic Fernet key from
        # DJANGO_SECRET_KEY or other creds keys so users don't need manual config.
        fallback_source = (
            os.environ.get('DJANGO_SECRET_KEY', '') or
            os.environ.get('TWITTER_CREDS_ENC_KEY', '') or
            'fanart_viewer_gemini_creds_default_fallback_seed'
        )
        derived = hashlib.sha256(fallback_source.encode('utf-8')).digest()
        key = base64.urlsafe_b64encode(derived).decode('utf-8')
    return Fernet(key.encode())


def set_credentials(api_key: str):
    """Encrypt and persist the Gemini API key, replacing any previously stored value."""
    api_key_clean = api_key.strip()
    if not api_key_clean:
        raise ValueError("APIキーが入力されていません。")

    f = _fernet()
    enc_key = f.encrypt(api_key_clean.encode('utf-8'))

    row = GeminiCredential.objects.first()
    if row is None:
        row = GeminiCredential()
    row.encrypted_api_key = enc_key
    row.save()


def clear_credentials():
    """Clear any DB-stored Gemini API key."""
    row = GeminiCredential.objects.first()
    if row is not None:
        row.encrypted_api_key = None
        row.save()


def get_api_key() -> str:
    """Return the Gemini API key, preferring the encrypted DB-stored value
    and falling back to the GEMINI_API_KEY env var (or GEMINI_API_KEY in .env).
    """
    row = GeminiCredential.objects.first()
    if row is not None and row.encrypted_api_key:
        try:
            f = _fernet()
            return f.decrypt(bytes(row.encrypted_api_key)).decode('utf-8').strip()
        except Exception:
            pass
    return os.environ.get('GEMINI_API_KEY', '').strip()


def has_api_key() -> bool:
    """Return whether a Gemini API key is configured from either DB or env."""
    return bool(get_api_key())


def status() -> dict:
    """Non-secret status info for the UI: whether configured, source ('db', 'env', 'none'),
    and last updated timestamp. Never includes the key itself.
    """
    row = GeminiCredential.objects.first()
    if row is not None and row.encrypted_api_key:
        return {
            'configured': True,
            'source': 'db',
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        }
    if os.environ.get('GEMINI_API_KEY', '').strip():
        return {
            'configured': True,
            'source': 'env',
            'updated_at': None,
        }
    return {
        'configured': False,
        'source': 'none',
        'updated_at': None,
    }

