import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin as _admin_only
from . import classifier_training


@csrf_exempt
@require_http_methods(['GET'])
def train_classifier_status_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    return JsonResponse(classifier_training.get_status())


@csrf_exempt
@require_http_methods(['POST'])
def train_classifier_start_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        options = json.loads(request.body or b'{}')
    except Exception:
        options = {}
    if not isinstance(options, dict):
        options = {}

    try:
        classifier_training.start(options)
    except ValueError as e:
        return JsonResponse({'detail': str(e)}, status=400)
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse(classifier_training.get_status())


@csrf_exempt
@require_http_methods(['POST'])
def train_classifier_stop_view(request):
    denied = _admin_only(request)
    if denied:
        return denied
    try:
        classifier_training.stop()
    except RuntimeError as e:
        return JsonResponse({'detail': str(e)}, status=409)
    return JsonResponse(classifier_training.get_status())
