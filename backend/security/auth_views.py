import os
import json
import time
import threading
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from security.token_utils import make_token, verify_token  # noqa: F401 (verify_token re-exported for middleware)

# ---------------------------------------------------------------------------
# Brute-force protection
# Configurable via environment variables (read once at import time).
# ---------------------------------------------------------------------------
_MAX_ATTEMPTS  = int(os.environ.get('LOGIN_MAX_ATTEMPTS', '5'))
_BLOCK_MINUTES = int(os.environ.get('LOGIN_BLOCK_MINUTES', '15'))
_BLOCK_SECONDS = _BLOCK_MINUTES * 60

_lock  = threading.Lock()
# ip → {'count': int, 'first': float, 'blocked_until': float}
_state: dict = {}


def _get_client_ip(request):
    """Return the real client IP, accounting for Cloudflare and other proxies."""
    for header in ('HTTP_CF_CONNECTING_IP', 'HTTP_X_REAL_IP'):
        v = request.META.get(header, '').strip()
        if v:
            return v
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _is_blocked(ip):
    """Returns (blocked: bool, retry_after_seconds: int)."""
    now = time.monotonic()
    with _lock:
        s = _state.get(ip)
        if s and s['blocked_until'] > now:
            return True, int(s['blocked_until'] - now) + 1
    return False, 0


def _record_failure(ip):
    """Increment failure counter. Returns (newly_blocked: bool, attempts_left: int)."""
    now = time.monotonic()
    with _lock:
        s = _state.setdefault(ip, {'count': 0, 'first': now, 'blocked_until': 0.0})
        # reset window if the block period has passed without a new block
        if s['blocked_until'] <= now and (now - s['first']) > _BLOCK_SECONDS:
            s['count'] = 0
            s['first'] = now
        s['count'] += 1
        if s['count'] >= _MAX_ATTEMPTS:
            s['blocked_until'] = now + _BLOCK_SECONDS
            return True, 0
        return False, _MAX_ATTEMPTS - s['count']


def _record_success(ip):
    with _lock:
        _state.pop(ip, None)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _set_auth_cookies(resp, token):
    MAX_AGE = 30 * 24 * 3600
    resp.set_cookie('fv_auth', token, max_age=MAX_AGE, httponly=True,  samesite='Lax')
    resp.set_cookie('fv_ext',  token, max_age=MAX_AGE, httponly=False, samesite='Lax')


def _blocked_response(retry_after):
    minutes = (retry_after + 59) // 60
    return JsonResponse(
        {'detail': f'ログイン試行回数の上限に達しました。{minutes}分後に再試行してください。'},
        status=429,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@csrf_exempt
def login_view(request):
    """Viewer-only login. Admin password is never accepted here."""
    if request.method == 'GET':
        viewer_pass = os.environ.get('VIEWER_PASSWORD', '')
        admin_pass  = os.environ.get('ADMIN_PASSWORD', '')
        return JsonResponse({'auth_required': bool(viewer_pass or admin_pass)})

    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    ip = _get_client_ip(request)
    blocked, retry_after = _is_blocked(ip)
    if blocked:
        return _blocked_response(retry_after)

    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    password = data.get('password', '')

    viewer_pass = os.environ.get('VIEWER_PASSWORD', '')
    if viewer_pass and password == viewer_pass:
        _record_success(ip)
        token = make_token('viewer')
        resp = JsonResponse({'token': token, 'role': 'viewer'})
        _set_auth_cookies(resp, token)
        return resp

    newly_blocked, remaining = _record_failure(ip)
    if newly_blocked:
        return _blocked_response(_BLOCK_SECONDS)
    return JsonResponse({'detail': f'パスワードが違います（残り{remaining}回）'}, status=401)


@csrf_exempt
def admin_login_view(request):
    """Admin-only login. Reachable only from the secret frontend path."""
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    ip = _get_client_ip(request)
    blocked, retry_after = _is_blocked(ip)
    if blocked:
        return _blocked_response(retry_after)

    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    password = data.get('password', '')

    admin_pass = os.environ.get('ADMIN_PASSWORD', '')
    if admin_pass and password == admin_pass:
        _record_success(ip)
        token = make_token('admin')
        resp = JsonResponse({'token': token, 'role': 'admin'})
        _set_auth_cookies(resp, token)
        return resp

    newly_blocked, remaining = _record_failure(ip)
    if newly_blocked:
        return _blocked_response(_BLOCK_SECONDS)
    return JsonResponse({'detail': f'パスワードが違います（残り{remaining}回）'}, status=401)
