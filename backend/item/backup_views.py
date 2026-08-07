import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import verify_token
from .drive_backup import create_backup, list_backups, restore_backup, DriveBackupError, ExistingDataError


def _admin_only(request):
    """Return a JsonResponse to short-circuit with, or None if allowed.

    Mirrors SimpleAuthMiddleware's "no password configured = open" posture,
    but additionally requires the admin role specifically (not just any
    authenticated write access) since backup/restore exposes Drive file
    metadata and can load data into the database.
    """
    admin_pass = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pass:
        return None  # auth not configured for this deployment — match app-wide behavior

    token = request.COOKIES.get('fv_auth', '') or request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if verify_token(token) != 'admin':
        return JsonResponse({'detail': '管理者のみ利用できます'}, status=403)
    return None


@csrf_exempt
@require_http_methods(['POST'])
def backup_create_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        meta = create_backup()
    except DriveBackupError as e:
        return JsonResponse({'detail': str(e)}, status=500)
    return JsonResponse(meta)


@csrf_exempt
@require_http_methods(['GET'])
def backup_list_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        files = list_backups()
    except DriveBackupError as e:
        return JsonResponse({'detail': str(e)}, status=500)
    return JsonResponse({'files': files})


@csrf_exempt
@require_http_methods(['POST'])
def backup_restore_view(request):
    denied = _admin_only(request)
    if denied:
        return denied

    import json
    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    file_id = data.get('file_id', '')
    overwrite = bool(data.get('overwrite', False))
    if not file_id:
        return JsonResponse({'detail': 'file_idが必要です'}, status=400)

    try:
        restore_backup(file_id, overwrite=overwrite)
    except ExistingDataError as e:
        return JsonResponse({
            'needs_confirmation': True,
            'current': e.current,
            'backup': e.backup,
        }, status=409)
    except DriveBackupError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse({'ok': True})
