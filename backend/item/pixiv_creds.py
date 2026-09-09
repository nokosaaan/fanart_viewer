"""Encrypted storage for Pixiv login (the PHPSESSID cookie, or a
username/password pair) used by playwright_helper.py's Pixiv fetcher, as
an alternative to setting PIXIV_PHPSESSID/PIXIV_USER/PIXIV_PASS in .env
(which requires recreating the `web` container to pick up a change, and
has no equivalent at all for the exe-packaged distribution, which has no
.env file).

Mirrors item.twitter_creds exactly: values are stored Fernet-encrypted
(reversible, not hashed — playwright_helper.py needs the actual cookie/
password back to log in with) under PIXIV_CREDS_ENC_KEY, kept out of the
DB entirely. Write-only from the HTTP API's perspective — see
item.pixiv_creds_views.
"""
import os

from .models import PixivCredential


class PixivCredsConfigError(RuntimeError):
    """PIXIV_CREDS_ENC_KEY missing while trying to read/write a stored credential."""


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get('PIXIV_CREDS_ENC_KEY', '').strip()
    if not key:
        raise PixivCredsConfigError(
            'PIXIV_CREDS_ENC_KEY is not set. Generate one with: '
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def set_credentials(phpsessid: str | None = None, user: str | None = None, password: str | None = None):
    """Encrypt and persist whichever of phpsessid/user/password are given
    (non-blank); anything left as None (or blank) keeps its previously
    stored value untouched — same partial-update behavior as
    twitter_creds.set_credentials's `twid` argument.
    """
    f = _fernet()
    row = PixivCredential.objects.first()
    if row is None:
        row = PixivCredential()
    if phpsessid is not None and phpsessid.strip():
        row.encrypted_phpsessid = f.encrypt(phpsessid.strip().encode())
    if user is not None and user.strip():
        row.encrypted_user = f.encrypt(user.strip().encode())
    if password is not None and password.strip():
        row.encrypted_password = f.encrypt(password.strip().encode())
    row.save()


def get_credentials() -> dict:
    """Return {'phpsessid': str, 'user': str, 'password': str}, each ''
    when unset. Prefers the encrypted DB-stored value per-field, falling
    back to PIXIV_PHPSESSID/PIXIV_USER/PIXIV_PASS env vars for fields
    never saved through the UI (so existing .env-based deployments keep
    working unchanged).
    """
    row = PixivCredential.objects.first()
    f = None

    def _decrypt(field):
        nonlocal f
        if not field:
            return ''
        if f is None:
            f = _fernet()
        return f.decrypt(bytes(field)).decode()

    phpsessid = _decrypt(row.encrypted_phpsessid) if row else ''
    user = _decrypt(row.encrypted_user) if row else ''
    password = _decrypt(row.encrypted_password) if row else ''

    return {
        'phpsessid': phpsessid or os.environ.get('PIXIV_PHPSESSID', '').strip(),
        'user': user or os.environ.get('PIXIV_USER', '').strip() or os.environ.get('PIXIV_USERNAME', '').strip(),
        'password': password or os.environ.get('PIXIV_PASS', '').strip() or os.environ.get('PIXIV_PASSWORD', '').strip(),
    }


def has_credentials() -> bool:
    creds = get_credentials()
    return bool(creds['phpsessid']) or bool(creds['user'] and creds['password'])


def status() -> dict:
    """Non-secret status info for the settings UI. Never includes the
    values themselves."""
    row = PixivCredential.objects.first()
    db_configured = bool(row and (row.encrypted_phpsessid or (row.encrypted_user and row.encrypted_password)))
    if db_configured:
        return {
            'configured': True, 'source': 'db', 'updated_at': row.updated_at.isoformat(),
            'has_phpsessid': bool(row.encrypted_phpsessid),
            'has_user_pass': bool(row.encrypted_user and row.encrypted_password),
        }
    env_phpsessid = os.environ.get('PIXIV_PHPSESSID', '').strip()
    env_user = os.environ.get('PIXIV_USER', '').strip() or os.environ.get('PIXIV_USERNAME', '').strip()
    env_pass = os.environ.get('PIXIV_PASS', '').strip() or os.environ.get('PIXIV_PASSWORD', '').strip()
    if env_phpsessid or (env_user and env_pass):
        return {
            'configured': True, 'source': 'env', 'updated_at': None,
            'has_phpsessid': bool(env_phpsessid), 'has_user_pass': bool(env_user and env_pass),
        }
    return {'configured': False, 'source': 'none', 'updated_at': None, 'has_phpsessid': False, 'has_user_pass': False}
