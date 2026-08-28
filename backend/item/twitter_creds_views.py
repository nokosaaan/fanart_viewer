"""Admin-only, write-only HTTP endpoints for the stored Twitter/X session
cookies (see twitter_creds.py for the encryption/storage itself).

Deliberately write-only: there is no view here that returns the stored
auth_token/ct0 value, encrypted or not, to any client — only whether
something is configured and when it was last changed. This means even an
admin session can't pull the secret back out through the API once saved;
the only way to see the actual cookie values is the browser session they
were originally copied from.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import twitter_creds


@require_http_methods(['GET'])
def twitter_creds_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(twitter_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def twitter_creds_set_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    try:
        import json
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    auth_token = (data.get('auth_token') or '').strip()
    ct0 = (data.get('ct0') or '').strip()
    if not auth_token or not ct0:
        return JsonResponse({'detail': 'auth_token and ct0 are both required'}, status=400)

    try:
        twitter_creds.set_credentials(auth_token, ct0)
    except twitter_creds.TwitterCredsConfigError as e:
        return JsonResponse({'detail': str(e)}, status=500)

    return JsonResponse(twitter_creds.status())
