"""Encrypted storage for Poipiku login cookies (POIPIKU_LK/JSESSIONID)
used by item.poipiku_fetch, as an alternative to setting them in .env
(which the exe-packaged distribution has no equivalent of at all -- no
.env file a user could edit). Mirrors item.twitter_creds/item.pixiv_creds
exactly: Fernet-encrypted under POIPIKU_CREDS_ENC_KEY, kept out of the DB
entirely, DB takes precedence with env vars as fallback for existing
.env-based deployments.
"""
import os

from .models import PoipikuCredential


class PoipikuCredsConfigError(RuntimeError):
    """POIPIKU_CREDS_ENC_KEY missing while trying to read/write a stored credential."""


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get('POIPIKU_CREDS_ENC_KEY', '').strip()
    if not key:
        raise PoipikuCredsConfigError(
            'POIPIKU_CREDS_ENC_KEY is not set. Generate one with: '
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def set_credentials(lk: str | None = None, jsessionid: str | None = None):
    """Encrypt and persist whichever of lk/jsessionid are given (non-blank);
    anything left as None (or blank) keeps its previously stored value
    untouched."""
    f = _fernet()
    row = PoipikuCredential.objects.first()
    if row is None:
        row = PoipikuCredential()
    if lk is not None and lk.strip():
        row.encrypted_lk = f.encrypt(lk.strip().encode())
    if jsessionid is not None and jsessionid.strip():
        row.encrypted_jsessionid = f.encrypt(jsessionid.strip().encode())
    row.save()


def get_credentials() -> dict:
    """Return {'lk': str, 'jsessionid': str}, each '' when unset. Prefers
    the encrypted DB-stored value per-field, falling back to
    POIPIKU_LK/POIPIKU_JSESSIONID env vars for fields never saved through
    the UI (so an existing .env-based deployment keeps working
    unchanged)."""
    row = PoipikuCredential.objects.first()
    f = None

    def _decrypt(field):
        nonlocal f
        if not field:
            return ''
        if f is None:
            f = _fernet()
        return f.decrypt(bytes(field)).decode()

    lk = _decrypt(row.encrypted_lk) if row else ''
    jsessionid = _decrypt(row.encrypted_jsessionid) if row else ''

    return {
        'lk': lk or os.environ.get('POIPIKU_LK', '').strip(),
        'jsessionid': jsessionid or os.environ.get('POIPIKU_JSESSIONID', '').strip(),
    }


def has_credentials() -> bool:
    creds = get_credentials()
    return bool(creds['lk'] or creds['jsessionid'])


def status() -> dict:
    """Non-secret status info for the settings UI. Never includes the
    values themselves."""
    row = PoipikuCredential.objects.first()
    db_configured = bool(row and (row.encrypted_lk or row.encrypted_jsessionid))
    if db_configured:
        return {
            'configured': True, 'source': 'db', 'updated_at': row.updated_at.isoformat(),
            'has_lk': bool(row.encrypted_lk), 'has_jsessionid': bool(row.encrypted_jsessionid),
        }
    env_lk = os.environ.get('POIPIKU_LK', '').strip()
    env_jsessionid = os.environ.get('POIPIKU_JSESSIONID', '').strip()
    if env_lk or env_jsessionid:
        return {
            'configured': True, 'source': 'env', 'updated_at': None,
            'has_lk': bool(env_lk), 'has_jsessionid': bool(env_jsessionid),
        }
    return {'configured': False, 'source': 'none', 'updated_at': None, 'has_lk': False, 'has_jsessionid': False}
