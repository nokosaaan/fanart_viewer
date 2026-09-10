"""Encrypted storage for the Google Drive OAuth client (client_id/
client_secret) and refresh_token used by item.drive_backup, as an
alternative to setting GOOGLE_DRIVE_CLIENT_ID/GOOGLE_DRIVE_CLIENT_SECRET/
GOOGLE_DRIVE_REFRESH_TOKEN in .env -- which the exe-packaged distribution
has no equivalent of at all (no .env file a user could edit).

Mirrors item.twitter_creds/item.pixiv_creds exactly: values are stored
Fernet-encrypted under DRIVE_CREDS_ENC_KEY, kept out of the DB entirely.
See item.drive_creds_views for the write-only status/set API, and its
authenticate endpoint for how refresh_token actually gets populated here
(running the same OAuth flow scripts/google_drive_auth.py already does
out-of-band, from inside a request instead of a human pasting the result
into .env).
"""
import os

from .models import DriveCredential


class DriveCredsConfigError(RuntimeError):
    """DRIVE_CREDS_ENC_KEY missing while trying to read/write a stored credential."""


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get('DRIVE_CREDS_ENC_KEY', '').strip()
    if not key:
        # Should never actually happen -- see TwitterCredsConfigError's
        # identical comment in item/twitter_creds.py for why this message
        # deliberately says nothing about env vars or terminal commands.
        raise DriveCredsConfigError('内部エラー: 暗号化キーが設定されていません。アプリを再起動しても直らない場合は開発者にご連絡ください。')
    return Fernet(key.encode())


def set_credentials(client_id: str | None = None, client_secret: str | None = None, refresh_token: str | None = None):
    """Encrypt and persist whichever of client_id/client_secret/refresh_token
    are given (non-blank); anything left as None (or blank) keeps its
    previously stored value untouched."""
    f = _fernet()
    row = DriveCredential.objects.first()
    if row is None:
        row = DriveCredential()
    if client_id is not None and client_id.strip():
        row.encrypted_client_id = f.encrypt(client_id.strip().encode())
    if client_secret is not None and client_secret.strip():
        row.encrypted_client_secret = f.encrypt(client_secret.strip().encode())
    if refresh_token is not None and refresh_token.strip():
        row.encrypted_refresh_token = f.encrypt(refresh_token.strip().encode())
    row.save()


def get_credentials() -> dict:
    """Return {'client_id': str, 'client_secret': str, 'refresh_token': str},
    each '' when unset. Prefers the encrypted DB-stored value per-field,
    falling back to GOOGLE_DRIVE_CLIENT_ID/GOOGLE_DRIVE_CLIENT_SECRET/
    GOOGLE_DRIVE_REFRESH_TOKEN env vars for fields never saved through the
    UI (so an existing .env-based deployment keeps working unchanged)."""
    row = DriveCredential.objects.first()
    f = None

    def _decrypt(field):
        nonlocal f
        if not field:
            return ''
        if f is None:
            f = _fernet()
        return f.decrypt(bytes(field)).decode()

    client_id = _decrypt(row.encrypted_client_id) if row else ''
    client_secret = _decrypt(row.encrypted_client_secret) if row else ''
    refresh_token = _decrypt(row.encrypted_refresh_token) if row else ''

    return {
        'client_id': client_id or os.environ.get('GOOGLE_DRIVE_CLIENT_ID', '').strip(),
        'client_secret': client_secret or os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET', '').strip(),
        'refresh_token': refresh_token or os.environ.get('GOOGLE_DRIVE_REFRESH_TOKEN', '').strip(),
    }


def has_credentials() -> bool:
    creds = get_credentials()
    return bool(creds['client_id'] and creds['client_secret'] and creds['refresh_token'])


def status() -> dict:
    """Non-secret status info for the settings UI. Never includes the
    values themselves."""
    row = DriveCredential.objects.first()
    db_configured = bool(row and row.encrypted_client_id and row.encrypted_client_secret and row.encrypted_refresh_token)
    if db_configured:
        return {'configured': True, 'source': 'db', 'updated_at': row.updated_at.isoformat()}
    creds = get_credentials()
    if creds['client_id'] and creds['client_secret'] and creds['refresh_token']:
        return {'configured': True, 'source': 'env', 'updated_at': None}
    return {'configured': False, 'source': 'none', 'updated_at': None}
