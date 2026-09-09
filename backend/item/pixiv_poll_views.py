"""Admin-only, read-only status endpoint for the Pixiv bookmark poller
(see item.management.commands.poll_pixiv_bookmarks). Mirrors
twitter_poll_views.py exactly, scoped to platform='pixiv'.
"""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from security.token_utils import require_admin
from .models import PixivPollState, SocialFetchQueueItem


@require_http_methods(['GET'])
def pixiv_poll_status_view(request):
    denied = require_admin(request)
    if denied:
        return denied

    state = PixivPollState.objects.first()
    pending_count = SocialFetchQueueItem.objects.filter(status='pending', platform='pixiv').count()

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
