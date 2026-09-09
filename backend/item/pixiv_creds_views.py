"""Admin-only, write-only HTTP endpoints for the stored Pixiv login (see
pixiv_creds.py for the encryption/storage itself). Mirrors
twitter_creds_views.py exactly, including the write-only guarantee: no
view here returns the stored phpsessid/user/password, only whether
something is configured and when it last changed.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import pixiv_creds


@require_http_methods(['GET'])
def pixiv_creds_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(pixiv_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def pixiv_creds_set_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    try:
        import json
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    phpsessid = data.get('phpsessid')
    user = data.get('user')
    password = data.get('password')
    if not any((phpsessid and phpsessid.strip(), user and user.strip(), password and password.strip())):
        return JsonResponse({'detail': 'phpsessid, or both user and password, must be provided'}, status=400)

    try:
        pixiv_creds.set_credentials(phpsessid=phpsessid, user=user, password=password)
    except pixiv_creds.PixivCredsConfigError as e:
        return JsonResponse({'detail': str(e)}, status=500)

    return JsonResponse(pixiv_creds.status())
