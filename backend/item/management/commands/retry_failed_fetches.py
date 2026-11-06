"""Puts bookmark/like queue rows that ended up 'failed' back in the queue.

Rows used to be marked 'failed' on the first error (a network outage could turn
a hundred items into permanent failures that nothing ever retried). Now a
failure keeps the row pending until item.social_queue.MAX_ATTEMPTS; this
command revives rows parked as 'failed' (including every row failed under the
old behaviour) so the pollers fetch them again. Rows whose item already has
preview images are marked 'skipped' instead -- "fetched" is decided by the item.

Dry run by default.

Usage:
  python manage.py retry_failed_fetches                  # report only
  python manage.py retry_failed_fetches --apply
  python manage.py retry_failed_fetches --apply --platform pixiv
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from item.models import SocialFetchQueueItem
from item.views import _find_item_by_url


class Command(BaseCommand):
    help = "Revives 'failed' bookmark/like queue rows (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes (default: report only).')
        parser.add_argument('--platform', choices=['twitter', 'pixiv', 'poipiku'], help='Only this platform.')

    def handle(self, *args, **options):
        rows = SocialFetchQueueItem.objects.filter(status='failed')
        if options['platform']:
            rows = rows.filter(platform=options['platform'])

        revived = already = 0
        for row in rows:
            item = _find_item_by_url(row.url)
            fetched = False
            if item is not None:
                try:
                    fetched = item.preview_images.exists() or bool(item.preview_data)
                except Exception:
                    fetched = False
            if fetched:
                already += 1
                if options['apply']:
                    row.status = 'skipped'
                    row.processed_at = timezone.now()
                    row.save(update_fields=['status', 'processed_at'])
            else:
                revived += 1
                if options['apply']:
                    row.status = 'pending'
                    row.attempts = 0
                    row.save(update_fields=['status', 'attempts'])

        summary = f'{revived} row(s) back to pending, {already} already fetched (marked skipped)'
        if options['apply']:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary + ' -- dry run, nothing saved. Re-run with --apply.'))
