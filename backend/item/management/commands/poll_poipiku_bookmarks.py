"""Continuously poll the logged-in Poipiku account's own bookmark list
("お気に入り", https://poipiku.com/MyBookmarkListPcV.jsp) for new works
and archive them. Mirrors poll_pixiv_bookmarks.py's overall shape almost
exactly (discovery -> enqueue -> drain, gated on PollerSettings, same
SocialFetchQueueItem table scoped to platform='poipiku') -- the browser
extension already covered "fetch this one Poipiku post I clicked on",
but there was no automated route for "keep archiving whatever I bookmark
on Poipiku itself" the way Twitter/Pixiv already had, until this.

Run as its own long-lived process (exe/launcher.py's poller thread calls
this the same way it calls poll_pixiv_bookmarks -- see that file), never
inside the `web` request/response cycle.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from item.models import Item, PoipikuPollState, PollerSettings, PreviewImage, SocialFetchQueueItem
from item.notify import notify_discord
from item.poipiku_bookmarks_fetch import PoipikuBookmarksError, fetch_account_bookmarks
from item.poipiku_creds import has_credentials
from item.views import _call_fetch_and_save_preview, _find_item_by_url

logger = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 360
MAX_PAGES_STEADY = 1
NOTIFY_REPEAT_AFTER_HOURS = 24


class Command(BaseCommand):
    help = (
        'Continuously poll the logged-in Poipiku account\'s bookmark list '
        '("お気に入り") for new works, archiving up to '
        'PollerSettings.items_per_tick per tick. No-ops entirely unless '
        'PollerSettings.enabled is set. Runs forever unless --once is passed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='Run a single tick then exit, instead of looping forever.',
        )
        parser.add_argument(
            '--tick-seconds', type=int, default=TICK_SECONDS_DEFAULT,
            help=f'Seconds between ticks (default: {TICK_SECONDS_DEFAULT}).',
        )

    def handle(self, *args, **options):
        tick_seconds = options['tick_seconds']
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception('poll_poipiku_bookmarks: unhandled error in tick')
            if options['once']:
                break
            time.sleep(tick_seconds)

    def _tick(self):
        if not has_credentials():
            logger.info('poll_poipiku_bookmarks: no Poipiku credentials configured, skipping tick')
            return

        poller_settings, _ = PollerSettings.objects.get_or_create(platform='poipiku')
        if not poller_settings.enabled:
            logger.info('poll_poipiku_bookmarks: disabled in settings, skipping tick')
            return

        state, _ = PoipikuPollState.objects.get_or_create(pk=1)
        try:
            self._discover(state, poller_settings.backfill_pages_per_tick)
        except PoipikuBookmarksError as e:
            self._record_failure(state, str(e))
        except Exception as e:
            logger.exception('poll_poipiku_bookmarks: discovery failed')
            self._record_failure(state, str(e))
        else:
            self._record_success(state)

        self._drain(poller_settings.items_per_tick)

    def _discover(self, state: PoipikuPollState, backfill_pages_per_tick: int):
        # A bare Item with no saved preview yet, or a failed queue row, is
        # NOT actually "done" -- treating either as a permanent stop signal
        # would silently wall off every OLDER, still-genuinely-unprocessed
        # bookmark the first time pagination reaches one (same bug fixed in
        # poll_twitter_updates.py's own known_ids computation -- see its
        # comment there for the full reasoning).
        known_ids = set(
            Item.objects.filter(source='poipiku_bookmark')
            .annotate(_has_preview_images=Exists(PreviewImage.objects.filter(item_id=OuterRef('pk'))))
            .filter(Q(_has_preview_images=True) | Q(preview_data__isnull=False))
            .values_list('external_id', flat=True)
        )
        known_ids |= set(
            SocialFetchQueueItem.objects.filter(platform='poipiku')
            .exclude(status='failed')
            .values_list('external_id', flat=True)
        )

        max_pages = backfill_pages_per_tick if not known_ids else MAX_PAGES_STEADY

        candidates, resume_url = fetch_account_bookmarks(
            known_ids, max_pages=max_pages, start_url=state.resume_url or None,
        )
        state.resume_url = resume_url
        state.save(update_fields=['resume_url'])

        self._enqueue_new(candidates)

    def _enqueue_new(self, candidates):
        # candidates arrive newest-first; reverse so the oldest-in-this-
        # batch is inserted (and therefore dequeued) first.
        for cand in reversed(candidates):
            illust_id = int(cand['illust_id'])
            url = cand['url']

            existing = _find_item_by_url(url)
            if existing is not None and self._has_preview(existing):
                continue  # already fetched -- nothing to do

            SocialFetchQueueItem.objects.get_or_create(
                platform='poipiku', external_id=illust_id,
                defaults={
                    'kind': 'bookmark', 'screen_name': cand.get('user_name') or '', 'url': url,
                    'description': cand.get('description') or '',
                },
            )

    @staticmethod
    def _has_preview(item: Item) -> bool:
        try:
            return item.preview_images.exists() or bool(item.preview_data)
        except Exception:
            return False

    def _record_success(self, state: PoipikuPollState):
        state.last_success_at = timezone.now()
        state.consecutive_failures = 0
        state.save(update_fields=['last_success_at', 'consecutive_failures'])

    def _record_failure(self, state: PoipikuPollState, message: str):
        from datetime import timedelta

        now = timezone.now()
        was_already_failing = state.consecutive_failures > 0
        state.last_error = message
        state.last_error_at = now
        state.consecutive_failures += 1

        should_notify = (
            not was_already_failing
            or state.last_notified_at is None
            or (now - state.last_notified_at) >= timedelta(hours=NOTIFY_REPEAT_AFTER_HOURS)
        )
        if should_notify:
            notify_discord(
                ':warning: fanart_viewer: Poipikuのお気に入り自動取得が失敗しています。\n'
                f'{message}\n'
                'POIPIKU_LK/JSESSIONID が期限切れの可能性があります。設定画面から再設定してください。'
            )
            state.last_notified_at = now

        state.save(update_fields=['last_error', 'last_error_at', 'consecutive_failures', 'last_notified_at'])

    # --- drain: process up to `max_items` pending queue rows per tick ----

    def _drain(self, max_items: int):
        fetched = 0
        while fetched < max_items:
            row = SocialFetchQueueItem.objects.filter(status='pending', platform='poipiku').order_by('id').first()
            if row is None:
                return

            existing = _find_item_by_url(row.url)
            if existing is not None and self._has_preview(existing):
                row.status = 'skipped'
                row.processed_at = timezone.now()
                row.save(update_fields=['status', 'processed_at'])
                continue  # doesn't count against this tick's fetch budget

            item = existing or Item.objects.create(
                external_id=row.external_id,
                source='poipiku_bookmark',
                situation='',
                titles=[],
                characters=[],
                artist=row.screen_name,
                link=row.url,
                tags=None,
                description=row.description,
            )

            ok = self._fetch_item(item, row.url)

            row.status = 'done' if ok else 'failed'
            row.processed_at = timezone.now()
            row.save(update_fields=['status', 'processed_at'])
            fetched += 1

    @staticmethod
    def _fetch_item(item: Item, url: str) -> bool:
        try:
            # fetch_and_save_preview already has dedicated Poipiku handling
            # for any poipiku.com URL regardless of force_method (see
            # item/poipiku_fetch.py's fetch_poipiku_media and its call site
            # in views.py), so no force_method override is needed here the
            # way Pixiv needs 'playwright' -- this whole command only ever
            # fetches poipiku.com links anyway.
            response = _call_fetch_and_save_preview(item.pk, {'url': url})
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception('poll_poipiku_bookmarks: fetch failed for item %s (%s)', item.pk, url)
            return False
