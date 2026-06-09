import os
import hmac
import hashlib
from django.http import JsonResponse


def _make_token(role: str) -> str:
    secret = os.environ.get('TOKEN_SECRET') or os.environ.get('DJANGO_SECRET_KEY', 'dev-secret')
    return hmac.new(secret.encode(), role.encode(), hashlib.sha256).hexdigest()


def verify_token(token: str):
    """Return 'admin', 'viewer', or None."""
    if not token:
        return None
    if token == _make_token('admin'):
        return 'admin'
    if token == _make_token('viewer'):
        return 'viewer'
    return None


class SimpleAuthMiddleware:
    """
    Optional auth layer. Only activates when VIEWER_PASSWORD or ADMIN_PASSWORD
    is set in the environment. When neither is set, all requests pass through
    unchanged (preserves backward-compatible local-only usage).
    """
    _WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        viewer_pass = os.environ.get('VIEWER_PASSWORD', '')
        admin_pass  = os.environ.get('ADMIN_PASSWORD', '')

        # Auth not configured — pass through (local dev, no passwords set)
        if not viewer_pass and not admin_pass:
            return self.get_response(request)

        # CORS preflight and the auth endpoint itself never require a token
        if request.method == 'OPTIONS' or request.path.startswith('/api/auth/'):
            return self.get_response(request)

        auth_header = request.headers.get('Authorization', '')
        token = auth_header.removeprefix('Bearer ').strip()
        # Also accept cookie-based auth for <img src> and other browser-native requests
        if not token:
            token = request.COOKIES.get('fv_auth', '')
        role = verify_token(token)

        if role is None:
            return JsonResponse({'detail': 'Authentication required'}, status=401)

        # viewer → read-only: block any state-changing method
        if role == 'viewer' and request.method in self._WRITE_METHODS:
            return JsonResponse({'detail': 'Read-only access'}, status=403)

        return self.get_response(request)
