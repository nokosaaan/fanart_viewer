"""Queue helpers shared by the bookmark/like pollers (poll_twitter_updates,
poll_pixiv_bookmarks, poll_poipiku_bookmarks).

"Fetched" is what the Item says (it has preview images), not what the queue row
says. A row whose fetch failed is therefore not finished: it goes back to the
queue and is tried again later, so a stretch of failures (a network outage, say)
does not leave a gap. Only after MAX_ATTEMPTS failures is it parked as 'failed',
and `manage.py retry_failed_fetches` puts those back.
"""
from django.utils import timezone

from .models import SocialFetchQueueItem

MAX_ATTEMPTS = 10


def next_pending(platform):
    """The next row to fetch: rows that have failed fewer times go first, so one
    broken item can't hold up the rest; within the same count, oldest first."""
    return (SocialFetchQueueItem.objects
            .filter(status='pending', platform=platform)
            .order_by('attempts', 'id')
            .first())


def record_fetch_result(row, ok):
    """Store the outcome of fetching `row`. Returns the new status."""
    row.processed_at = timezone.now()
    if ok:
        row.status = 'done'
    else:
        row.attempts += 1
        row.status = 'failed' if row.attempts >= MAX_ATTEMPTS else 'pending'
    row.save(update_fields=['status', 'processed_at', 'attempts'])
    return row.status
