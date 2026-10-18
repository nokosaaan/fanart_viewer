"""Backfills Pixiv's author-set tags into Item.description for existing
Pixiv items. Before this, a Pixiv item's description was just the work's
title (or empty), so item.views._match_hashtags -- which reads "#tag"
tokens out of the description -- never saw the tags the artist set, even
though they are the single most reliable title/character signal there is.

Tags are appended to whatever description is already there (as "#tag"
tokens, see item.pixiv_bookmarks_fetch.build_description), never replacing
it; an item whose description already contains a "#tag" is left alone.
Goes through the same Playwright + PHPSESSID request context the bookmark
poller uses, one browser for the whole batch, and stamps
description_checked_at so a re-run doesn't re-query items it already
answered (an item with no tags at all is stamped too).

Run with --dry-run first: it prints the tags it WOULD add for the first few
items, which is the quickest way to confirm Pixiv's /ajax/illust/{id}
response still has the shape fetch_illust_tags expects.

Usage:
  python manage.py backfill_pixiv_tags --dry-run --limit 5
  python manage.py backfill_pixiv_tags --limit 50
"""
import re
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from item.models import Item
from item.pixiv_bookmarks_fetch import (
    PixivAuthError, _fetch_illust_body, _pixiv_request_context, _tags_from_body, build_description,
)

DEFAULT_LIMIT = 30
DEFAULT_SLEEP_SECONDS = 1.0
_ARTWORK_RE = re.compile(r'pixiv\.net/(?:\w+/)?artworks/(\d+)')


class Command(BaseCommand):
    help = "Appends Pixiv's author-set tags (as #hashtags) to existing Pixiv items' descriptions."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                            help=f'Max items per run (default {DEFAULT_LIMIT}; 0 = no cap).')
        parser.add_argument('--sleep', type=float, default=DEFAULT_SLEEP_SECONDS,
                            help=f'Seconds between requests (default {DEFAULT_SLEEP_SECONDS}).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be added without saving anything.')

    def handle(self, *args, **options):
        queryset = Item.objects.filter(link__icontains='pixiv.net/').filter(
            description_checked_at__isnull=True,
        ).order_by('-id')
        candidates = [it for it in queryset.iterator() if '#' not in (it.description or '') and _ARTWORK_RE.search(it.link or '')]
        total = len(candidates)
        if options['limit'] and options['limit'] > 0:
            candidates = candidates[:options['limit']]
        self.stdout.write(f'{total} Pixiv item(s) without tags -- processing {len(candidates)} this run.')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: nothing will be saved.'))

        updated = no_tags = failed = 0
        try:
            with _pixiv_request_context() as ctx:
                for i, item in enumerate(candidates, start=1):
                    illust_id = int(_ARTWORK_RE.search(item.link).group(1))
                    try:
                        body = _fetch_illust_body(ctx, illust_id)
                        tags = _tags_from_body(body)
                    except PixivAuthError as e:
                        self.stderr.write(self.style.ERROR(f'Auth error -- stopping (PHPSESSID expired?): {e}'))
                        break
                    except Exception as e:
                        self.stderr.write(f'item {item.id} (illust {illust_id}): {e}, skipping')
                        failed += 1
                        continue

                    if tags:
                        base = (item.description or '').strip() or (body.get('title') or '')
                        new_description = build_description(base, tags)
                        updated += 1
                        if options['dry_run']:
                            self.stdout.write(f'  item {item.id}: +{len(tags)} tag(s): {tags[:10]}')
                        else:
                            item.description = new_description
                            item.description_checked_at = timezone.now()
                            item.save(update_fields=['description', 'description_checked_at'])
                    else:
                        no_tags += 1
                        if not options['dry_run']:
                            item.description_checked_at = timezone.now()
                            item.save(update_fields=['description_checked_at'])

                    if i % 10 == 0 or i == len(candidates):
                        self.stdout.write(f'{i}/{len(candidates)} (updated={updated} no_tags={no_tags} failed={failed})')
                    if i < len(candidates):
                        time.sleep(options['sleep'])
        except RuntimeError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Done. updated={updated} no_tags={no_tags} failed={failed}' + (' (dry-run)' if options['dry_run'] else '')
        ))
