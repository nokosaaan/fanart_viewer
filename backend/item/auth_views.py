import os
import hmac
import hashlib
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def _make_token(role: str) -> str:
    secret = os.environ.get('TOKEN_SECRET') or os.environ.get('DJANGO_SECRET_KEY', 'dev-secret')
    return hmac.new(secret.encode(), role.encode(), hashlib.sha256).hexdigest()


def _set_auth_cookies(resp, token):
    MAX_AGE = 30 * 24 * 3600
    resp.set_cookie('fv_auth', token, max_age=MAX_AGE, httponly=True,  samesite='Lax')
    resp.set_cookie('fv_ext',  token, max_age=MAX_AGE, httponly=False, samesite='Lax')


@csrf_exempt
def login_view(request):
    """Viewer-only login. Admin password is never accepted here."""
    if request.method == 'GET':
        viewer_pass = os.environ.get('VIEWER_PASSWORD', '')
        admin_pass  = os.environ.get('ADMIN_PASSWORD', '')
        return JsonResponse({'auth_required': bool(viewer_pass or admin_pass)})

    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    password = data.get('password', '')

    viewer_pass = os.environ.get('VIEWER_PASSWORD', '')
    if viewer_pass and password == viewer_pass:
        token = _make_token('viewer')
        resp = JsonResponse({'token': token, 'role': 'viewer'})
        _set_auth_cookies(resp, token)
        return resp
    return JsonResponse({'detail': 'パスワードが違います'}, status=401)


@csrf_exempt
def admin_login_view(request):
    """Admin-only login. Reachable only from the secret frontend path."""
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    password = data.get('password', '')

    admin_pass = os.environ.get('ADMIN_PASSWORD', '')
    if admin_pass and password == admin_pass:
        token = _make_token('admin')
        resp = JsonResponse({'token': token, 'role': 'admin'})
        _set_auth_cookies(resp, token)
        return resp
    return JsonResponse({'detail': 'パスワードが違います'}, status=401)
