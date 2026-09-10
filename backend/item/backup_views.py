from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin as _admin_only
from . import backup_progress
from . import restore_progress
from .drive_backup import list_backups, get_backup_folder_url, DriveBackupError


@csrf_exempt
@require_http_methods(['POST'])
def backup_create_view(request):
    """Starts a backup on a background thread and returns immediately —
    BackupManager.jsx polls backup_status_view for progress instead of
    this request blocking until the whole thing finishes (see
    backup_progress.py for why)."""
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        backup_progress.start()
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse(backup_progress.get_status())


@csrf_exempt
@require_http_methods(['GET'])
def backup_status_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    return JsonResponse(backup_progress.get_status())


@csrf_exempt
@require_http_methods(['GET'])
def backup_list_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        files = list_backups()
        folder_url = get_backup_folder_url()
    except DriveBackupError as e:
        return JsonResponse({'detail': str(e)}, status=500)
    return JsonResponse({'files': files, 'folder_url': folder_url})


@csrf_exempt
@require_http_methods(['POST'])
def backup_restore_view(request):
    """Starts a restore on a background thread and returns immediately —
    BackupManager.jsx polls backup_restore_status_view instead of this
    request blocking (see restore_progress.py). The existing-data
    confirmation (needs_confirmation) is now a state the status endpoint
    reports, not a synchronous 409 from this same call — a restore that
    turns out to need confirmation touches nothing, and the frontend
    re-POSTs here with the user's chosen mode once they decide."""
    denied = _admin_only(request)
    if denied:
        return denied

    import json
    try:
        data = json.loads(request.body or b'{}')
    except Exception:
        data = {}
    file_id = data.get('file_id', '')
    mode = data.get('mode') or ('overwrite' if bool(data.get('overwrite', False)) else 'strict')
    if mode not in ('strict', 'overwrite', 'merge'):
        return JsonResponse({'detail': f"mode must be one of 'strict', 'overwrite', 'merge' (got {mode!r})"}, status=400)
    if not file_id:
        return JsonResponse({'detail': 'file_idが必要です'}, status=400)

    try:
        restore_progress.start(file_id, mode)
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse(restore_progress.get_status())


@csrf_exempt
@require_http_methods(['GET'])
def backup_restore_status_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    return JsonResponse(restore_progress.get_status())
