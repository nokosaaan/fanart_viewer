"""Admin-only, write-only HTTP endpoints for stored Gemini API key (see
gemini_creds.py for the encryption/storage itself).
"""
import json
import urllib.request
import urllib.error

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from . import gemini_creds


@require_http_methods(['GET'])
def gemini_creds_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    return JsonResponse(gemini_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def gemini_creds_set_view(request):
    denied = require_admin(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    api_key = data.get('api_key', '')
    clear = data.get('clear', False)

    if clear:
        gemini_creds.clear_credentials()
        return JsonResponse(gemini_creds.status())

    if not api_key or not api_key.strip():
        return JsonResponse({'detail': 'APIキーを入力してください。'}, status=400)

    try:
        gemini_creds.set_credentials(api_key=api_key.strip())
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

    return JsonResponse(gemini_creds.status())


@csrf_exempt
@require_http_methods(['POST'])
def gemini_creds_test_view(request):
    """Test the Gemini API key against Google's API to verify validity."""
    denied = require_admin(request)
    if denied:
        return denied

    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        data = {}

    # Test key in request body if provided, otherwise test currently configured key
    test_key = data.get('api_key', '').strip() or gemini_creds.get_api_key()
    if not test_key:
        return JsonResponse({'success': False, 'detail': 'テストするAPIキーがありません。'}, status=400)

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={test_key}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FanartViewer/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return JsonResponse({'success': True, 'detail': 'APIキーの疎通テストに成功しました！'})
            return JsonResponse({'success': False, 'detail': f'APIレスポンスコード: {resp.status}'}, status=400)
    except urllib.error.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8', errors='ignore')
            err_json = json.loads(err_body)
            err_msg = err_json.get('error', {}).get('message', '')
            if err_msg:
                return JsonResponse({'success': False, 'detail': f'エラー: {err_msg}'}, status=400)
        except Exception:
            pass
        return JsonResponse({'success': False, 'detail': f'APIエラー: {e.code} {e.reason}'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'detail': f'接続エラー: {str(e)}'}, status=500)

