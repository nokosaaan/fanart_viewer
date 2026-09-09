"""Admin-only endpoints for the stored Google Drive OAuth client/refresh
token (see drive_creds.py). Mirrors twitter_creds_views.py's status/set
shape, plus one extra endpoint (`authenticate`) that runs the OAuth
consent flow itself -- the same flow scripts/google_drive_auth.py already
does out-of-band (InstalledAppFlow.run_local_server, opening the user's
system default browser to Google's consent screen and capturing the
result on a temporary local HTTP listener) -- so a user who can't edit
.env (the exe distribution) can still complete it from the settings
panel instead of needing a human to paste a printed refresh_token there.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import drive_creds

_SCOPES = ['https://www.googleapis.com/auth/drive.file']
_AUTH_TIMEOUT_SECONDS = 300


@require_http_methods(['GET'])
def drive_creds_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(drive_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def drive_creds_authenticate_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    client_id = (data.get('client_id') or '').strip()
    client_secret = (data.get('client_secret') or '').strip()
    if not client_id or not client_secret:
        # Allow re-authenticating (e.g. after a refresh_token is revoked)
        # without retyping the client id/secret every time.
        existing = drive_creds.get_credentials()
        client_id = client_id or existing['client_id']
        client_secret = client_secret or existing['client_secret']
    if not client_id or not client_secret:
        return JsonResponse({'detail': 'client_id と client_secret の両方が必要です'}, status=400)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return JsonResponse({'detail': 'google-auth-oauthlib がインストールされていません'}, status=500)

    client_config = {
        'installed': {
            'client_id': client_id,
            'client_secret': client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }
    }
    try:
        flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)
        # Blocks this request until the user finishes the consent flow in
        # their system browser (opened automatically) or the timeout
        # elapses -- acceptable here since this is an explicit one-time
        # admin action, not a normal request path.
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=_AUTH_TIMEOUT_SECONDS)
    except Exception as e:
        return JsonResponse({'detail': f'認証に失敗しました: {e}'}, status=502)

    if not creds.refresh_token:
        return JsonResponse({
            'detail': (
                '認証は成功しましたが refresh_token を取得できませんでした。'
                'Google側で既にこのアプリに許可済みの場合に起こることがあります。'
                'https://myaccount.google.com/permissions でこのアプリのアクセスを一度取り消してから、'
                '再度お試しください。'
            ),
        }, status=502)

    drive_creds.set_credentials(client_id=client_id, client_secret=client_secret, refresh_token=creds.refresh_token)
    return JsonResponse(drive_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def drive_creds_set_view(request):
    """Manual entry path (paste an already-obtained refresh_token, e.g.
    one generated via scripts/google_drive_auth.py on another machine) --
    the authenticate endpoint above is the normal path for the exe build."""
    denied = require_admin(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    client_id = data.get('client_id')
    client_secret = data.get('client_secret')
    refresh_token = data.get('refresh_token')
    if not any((client_id, client_secret, refresh_token)):
        return JsonResponse({'detail': 'client_id, client_secret, refresh_token のいずれかが必要です'}, status=400)

    drive_creds.set_credentials(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
    return JsonResponse(drive_creds.status())
