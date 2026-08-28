"""Encrypted storage for the Twitter/X session cookies (auth_token, ct0)
used by the scraping fetchers, as an alternative to setting
TWITTER_AUTH_TOKEN/TWITTER_CT0 in .env (which requires recreating the `web`
container to pick up a change).

Values are stored in the DB (TwitterCredential, a single row) encrypted
with Fernet (symmetric, reversible — NOT a password hash: these tokens are
bearer credentials sent to Twitter's API on every fetch, so the server must
be able to recover the original value, which a one-way hash would make
impossible). The decryption key lives only in TWITTER_CREDS_ENC_KEY, kept
out of the DB entirely, so a DB-only leak (e.g. the Google Drive backup)
can't be turned back into usable cookies without also having that key.

This module never exposes the decrypted values through any return value
meant for an HTTP response — get_credentials() is for internal fetcher use
only. See item.twitter_creds_views for the admin-only, write-only API.
"""
import os

from .models import TwitterCredential


class TwitterCredsConfigError(RuntimeError):
    """TWITTER_CREDS_ENC_KEY missing while trying to read/write a stored credential."""


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get('TWITTER_CREDS_ENC_KEY', '').strip()
    if not key:
        raise TwitterCredsConfigError(
            'TWITTER_CREDS_ENC_KEY is not set. Generate one with: '
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def set_credentials(auth_token: str, ct0: str):
    """Encrypt and persist auth_token/ct0, replacing any previously stored value."""
    f = _fernet()
    enc_auth = f.encrypt(auth_token.strip().encode())
    enc_ct0 = f.encrypt(ct0.strip().encode())

    row = TwitterCredential.objects.first()
    if row is None:
        row = TwitterCredential()
    row.encrypted_auth_token = enc_auth
    row.encrypted_ct0 = enc_ct0
    row.save()


def get_credentials() -> tuple[str, str]:
    """Return (auth_token, ct0), preferring the encrypted DB-stored value and
    falling back to the TWITTER_AUTH_TOKEN/TWITTER_CT0 env vars when no row
    has been saved yet (so existing .env-based deployments keep working
    unchanged until someone uses the new admin UI).
    """
    row = TwitterCredential.objects.first()
    if row is not None and row.encrypted_auth_token and row.encrypted_ct0:
        f = _fernet()
        return (
            f.decrypt(bytes(row.encrypted_auth_token)).decode(),
            f.decrypt(bytes(row.encrypted_ct0)).decode(),
        )
    return (
        os.environ.get('TWITTER_AUTH_TOKEN', '').strip(),
        os.environ.get('TWITTER_CT0', '').strip(),
    )


def has_credentials() -> bool:
    auth_token, ct0 = get_credentials()
    return bool(auth_token and ct0)


def status() -> dict:
    """Non-secret status info for the admin UI: whether something is
    configured and where it came from, and when it was last updated (DB
    source only — env vars carry no timestamp). Never includes the values
    themselves.
    """
    row = TwitterCredential.objects.first()
    if row is not None and row.encrypted_auth_token and row.encrypted_ct0:
        return {'configured': True, 'source': 'db', 'updated_at': row.updated_at.isoformat()}
    if os.environ.get('TWITTER_AUTH_TOKEN') and os.environ.get('TWITTER_CT0'):
        return {'configured': True, 'source': 'env', 'updated_at': None}
    return {'configured': False, 'source': 'none', 'updated_at': None}
