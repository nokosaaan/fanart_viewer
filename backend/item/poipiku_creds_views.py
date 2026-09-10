"""Admin-only, write-only HTTP endpoints for the stored Poipiku login
cookies (see poipiku_creds.py). Mirrors twitter_creds_views.py/
pixiv_creds_views.py exactly.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import poipiku_creds


@require_http_methods(['GET'])
def poipiku_creds_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(poipiku_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def poipiku_creds_set_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    try:
        import json
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    lk = data.get('lk')
    jsessionid = data.get('jsessionid')
    if not any((lk and lk.strip(), jsessionid and jsessionid.strip())):
        return JsonResponse({'detail': 'lk, jsessionid のいずれかが必要です'}, status=400)

    try:
        poipiku_creds.set_credentials(lk=lk, jsessionid=jsessionid)
    except poipiku_creds.PoipikuCredsConfigError as e:
        return JsonResponse({'detail': str(e)}, status=500)

    return JsonResponse(poipiku_creds.status())
