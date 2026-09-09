"""Admin-only, read-only status endpoint for the Twitter bookmark poller
(see item.management.commands.poll_twitter_updates). Surfaces
TwitterPollState + the pending queue depth so the frontend can show a
banner when polling has stopped working (auth expired, query IDs stale,
etc.) instead of it failing silently for months.
"""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from .models import SocialFetchQueueItem, TwitterPollState


@require_http_methods(['GET'])
def twitter_poll_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied

    state = TwitterPollState.objects.first()
    # kind='bookmark' only — the poller no longer touches Likes at all
    # (see poll_twitter_updates.py's own docstring), so a leftover/stray
    # 'like' row here would just inflate this health indicator with a
    # backlog the automatic poller was never going to process anyway.
    pending_count = SocialFetchQueueItem.objects.filter(status='pending', kind='bookmark').count()

    if state is None:
        return JsonResponse({
            'last_success_at': None,
            'last_error': '',
            'last_error_at': None,
            'consecutive_failures': 0,
            'pending_count': pending_count,
        })

    return JsonResponse({
        'last_success_at': state.last_success_at.isoformat() if state.last_success_at else None,
        'last_error': state.last_error,
        'last_error_at': state.last_error_at.isoformat() if state.last_error_at else None,
        'consecutive_failures': state.consecutive_failures,
        'pending_count': pending_count,
    })
