import os
from django.http import JsonResponse
from security.token_utils import verify_token


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

        # Only the /api/ surface is gated. The SPA shell and static assets
        # (served by the catch-all spa_fallback view for any non-API path)
        # must stay public: the React app has to load its JS bundle and call
        # /api/auth/ before it can even show a login form, so gating page
        # loads themselves would make the app un-loadable for anyone who
        # doesn't already have a valid token — precisely the visitor this
        # login screen exists for.
        if (request.method == 'OPTIONS'
                or not request.path.startswith('/api/')
                or request.path.startswith('/api/auth/')):
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
