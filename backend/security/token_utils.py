"""
Shared token generation and verification for fanart_viewer auth.

Token format: "{issued_at}:{hmac}"
  - issued_at : Unix timestamp (int) when the token was issued
  - hmac      : HMAC-SHA256( secret, "{role}:{issued_at}" )

The timestamp is embedded in the token so the server can check expiry
without keeping any server-side state. The HMAC covers both the role and
the timestamp, so neither field can be tampered with independently.
"""

import os
import hmac
import hashlib
import time

# Read once at import time; restart required to pick up changes.
_EXPIRY_SECONDS = int(os.environ.get('TOKEN_EXPIRY_DAYS', '30')) * 86400


def _secret() -> bytes:
    s = os.environ.get('TOKEN_SECRET') or os.environ.get('DJANGO_SECRET_KEY', 'dev-secret')
    return s.encode()


def make_token(role: str) -> str:
    """Return a new time-stamped token for the given role."""
    issued_at = int(time.time())
    msg = f'{role}:{issued_at}'.encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return f'{issued_at}:{sig}'


def verify_token(token: str):
    """
    Verify a token and return its role ('admin' or 'viewer'), or None.

    Returns None when:
      - token is empty / malformed
      - HMAC signature does not match (tampered or wrong secret)
      - token is older than TOKEN_EXPIRY_DAYS
    """
    if not token or ':' not in token:
        return None

    try:
        issued_at_str, sig = token.split(':', 1)
        issued_at = int(issued_at_str)
    except (ValueError, TypeError):
        return None

    if int(time.time()) - issued_at > _EXPIRY_SECONDS:
        return None  # expired

    secret = _secret()
    for role in ('admin', 'viewer'):
        msg = f'{role}:{issued_at}'.encode()
        expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return role

    return None  # signature mismatch
