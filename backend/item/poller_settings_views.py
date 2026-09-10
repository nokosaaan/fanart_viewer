"""Admin-only settings for the background pollers (see
item.models.PollerSettings, item.management.commands.poll_twitter_updates
and poll_pixiv_bookmarks). One independent row per platform ('twitter'/
'pixiv') -- see PollerSettings' own docstring for why. Unlike the
credential panels, this data isn't secret, so status/set both return the
full row.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from .models import PollerSettings

_UNITS = {choice for choice, _ in PollerSettings.UNIT_CHOICES}
_PLATFORMS = {choice for choice, _ in PollerSettings.PLATFORM_CHOICES}


def _serialize(row: PollerSettings) -> dict:
    return {
        'platform': row.platform,
        'enabled': row.enabled,
        'items_per_tick': row.items_per_tick,
        'interval_value': row.interval_value,
        'interval_unit': row.interval_unit,
        'interval_seconds': row.interval_seconds,
        'backfill_pages_per_tick': row.backfill_pages_per_tick,
        'updated_at': row.updated_at.isoformat(),
    }


@require_http_methods(['GET'])
def poller_settings_status_view(request, platform):
    denied = require_admin(request)
    if denied:
        return denied
    if platform not in _PLATFORMS:
        return JsonResponse({'detail': f'platform must be one of {sorted(_PLATFORMS)}'}, status=400)
    row, _ = PollerSettings.objects.get_or_create(platform=platform)
    return JsonResponse(_serialize(row))


@csrf_exempt
@require_http_methods(['POST'])
def poller_settings_set_view(request, platform):
    denied = require_admin(request)
    if denied:
        return denied
    if platform not in _PLATFORMS:
        return JsonResponse({'detail': f'platform must be one of {sorted(_PLATFORMS)}'}, status=400)
    try:
        import json
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    try:
        items_per_tick = int(data.get('items_per_tick', 1))
    except (TypeError, ValueError):
        return JsonResponse({'detail': 'items_per_tick must be an integer'}, status=400)
    if items_per_tick < 1:
        return JsonResponse({'detail': 'items_per_tick must be at least 1'}, status=400)

    try:
        interval_value = int(data.get('interval_value', 1))
    except (TypeError, ValueError):
        return JsonResponse({'detail': 'interval_value must be an integer'}, status=400)
    if interval_value < 1:
        return JsonResponse({'detail': 'interval_value must be at least 1'}, status=400)

    interval_unit = data.get('interval_unit', 'minutes')
    if interval_unit not in _UNITS:
        return JsonResponse({'detail': f'interval_unit must be one of {sorted(_UNITS)}'}, status=400)

    try:
        backfill_pages_per_tick = int(data.get('backfill_pages_per_tick', 3))
    except (TypeError, ValueError):
        return JsonResponse({'detail': 'backfill_pages_per_tick must be an integer'}, status=400)
    if backfill_pages_per_tick < 1:
        return JsonResponse({'detail': 'backfill_pages_per_tick must be at least 1'}, status=400)

    row, _ = PollerSettings.objects.get_or_create(platform=platform)
    row.enabled = bool(data.get('enabled', False))
    row.items_per_tick = items_per_tick
    row.interval_value = interval_value
    row.interval_unit = interval_unit
    row.backfill_pages_per_tick = backfill_pages_per_tick
    row.save(update_fields=[
        'enabled', 'items_per_tick', 'interval_value', 'interval_unit',
        'backfill_pages_per_tick', 'updated_at',
    ])

    return JsonResponse(_serialize(row))
