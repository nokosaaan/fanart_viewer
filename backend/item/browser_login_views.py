"""Admin-only endpoints fronting browser_login.py — see that module's
docstring for the full "why a real browser instead of DevTools" design."""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import browser_login

_PLATFORMS = ('twitter', 'pixiv', 'poipiku')


def _platform_from_body(request):
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        data = {}
    platform = (data.get('platform') or '').strip()
    if platform not in _PLATFORMS:
        return None
    return platform


@require_http_methods(['GET'])
def browser_login_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(browser_login.get_status())


@csrf_exempt
@require_http_methods(['POST'])
def browser_login_open_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    platform = _platform_from_body(request)
    if not platform:
        return JsonResponse({'detail': f'platform must be one of {_PLATFORMS}'}, status=400)
    try:
        browser_login.open_login(platform)
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse(browser_login.get_status())


@csrf_exempt
@require_http_methods(['POST'])
def browser_login_capture_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    platform = _platform_from_body(request)
    if not platform:
        return JsonResponse({'detail': f'platform must be one of {_PLATFORMS}'}, status=400)
    try:
        status = browser_login.capture_and_apply(platform)
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=422)
    return JsonResponse(status)


@csrf_exempt
@require_http_methods(['POST'])
def browser_login_cancel_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    platform = _platform_from_body(request)
    if not platform:
        return JsonResponse({'detail': f'platform must be one of {_PLATFORMS}'}, status=400)
    try:
        browser_login.cancel_login(platform)
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse({'ok': True})
