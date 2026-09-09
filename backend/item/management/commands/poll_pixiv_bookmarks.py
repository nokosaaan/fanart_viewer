"""Continuously poll the logged-in Pixiv account's own bookmarks for new
illustrations and archive them. Mirrors poll_twitter_updates.py's overall
shape (discovery -> enqueue -> drain, gated on PollerSettings, same
SocialFetchQueueItem table scoped to platform='pixiv') but considerably
simpler, since Pixiv's bookmarks endpoint has no separate "Likes" concept
and is directly offset-paginated rather than opaque-cursor-paginated (see
item.pixiv_bookmarks_fetch and PixivPollState.resume_offset).

Run as its own long-lived process (exe/launcher.py's poller thread calls
this the same way it calls poll_twitter_updates -- see that file), never
inside the `web` request/response cycle.
"""
import logging
import time
from types import SimpleNamespace

from django.core.management.base import BaseCommand
from django.utils import timezone

from item.models import Item, PixivPollState, PollerSettings, SocialFetchQueueItem
from item.notify import notify_discord
from item.pixiv_bookmarks_fetch import (
    PixivAPIError,
    PixivAuthError,
    fetch_account_bookmarks,
    resolve_own_user_id,
)
from item.pixiv_creds import has_credentials
from item.views import ItemViewSet, _find_item_by_url

logger = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 360
MAX_PAGES_STEADY = 1
MAX_PAGES_BACKFILL = 3  # only used the very first time there's no history at all yet
NOTIFY_REPEAT_AFTER_HOURS = 24


class Command(BaseCommand):
    help = (
        'Continuously poll the logged-in Pixiv account\'s bookmarks for '
        'new items, archiving up to PollerSettings.items_per_tick per '
        'tick. No-ops entirely unless PollerSettings.enabled is set. '
        'Runs forever unless --once is passed.'
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
                logger.exception('poll_pixiv_bookmarks: unhandled error in tick')
            if options['once']:
                break
            time.sleep(tick_seconds)

    def _tick(self):
        if not has_credentials():
            logger.info('poll_pixiv_bookmarks: no Pixiv credentials configured, skipping tick')
            return

        poller_settings, _ = PollerSettings.objects.get_or_create(pk=1)
        if not poller_settings.enabled:
            logger.info('poll_pixiv_bookmarks: disabled in settings, skipping tick')
            return

        state, _ = PixivPollState.objects.get_or_create(pk=1)
        try:
            self._discover(state)
        except (PixivAuthError, PixivAPIError) as e:
            self._record_failure(state, str(e))
        except Exception as e:
            logger.exception('poll_pixiv_bookmarks: discovery failed')
            self._record_failure(state, str(e))
        else:
            self._record_success(state)

        self._drain(poller_settings.items_per_tick)

    def _discover(self, state: PixivPollState):
        user_id = resolve_own_user_id()  # raises PixivAuthError/PixivAPIError -- let _tick's caller handle it

        known_ids = set(
            Item.objects.filter(source='pixiv_bookmark').values_list('external_id', flat=True)
        )
        known_ids |= set(
            SocialFetchQueueItem.objects.filter(platform='pixiv').values_list('external_id', flat=True)
        )

        max_pages = MAX_PAGES_BACKFILL if not known_ids else MAX_PAGES_STEADY

        candidates, resume_offset = fetch_account_bookmarks(
            user_id, known_ids, max_pages=max_pages, start_offset=state.resume_offset,
        )
        state.resume_offset = resume_offset
        state.save(update_fields=['resume_offset'])

        self._enqueue_new(candidates)

    def _enqueue_new(self, candidates):
        # candidates arrive newest-first; reverse so the oldest-in-this-
        # batch is inserted (and therefore dequeued) first.
        for cand in reversed(candidates):
            illust_id = int(cand['illust_id'])
            url = f'https://www.pixiv.net/artworks/{illust_id}'

            existing = _find_item_by_url(url)
            if existing is not None and self._has_preview(existing):
                continue  # already fetched -- nothing to do

            SocialFetchQueueItem.objects.get_or_create(
                platform='pixiv', external_id=illust_id,
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

    def _record_success(self, state: PixivPollState):
        state.last_success_at = timezone.now()
        state.consecutive_failures = 0
        state.save(update_fields=['last_success_at', 'consecutive_failures'])

    def _record_failure(self, state: PixivPollState, message: str):
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
                ':warning: fanart_viewer: Pixivのブックマーク自動取得が失敗しています。\n'
                f'{message}\n'
                'PHPSESSID が期限切れの可能性があります。設定画面から再設定してください。'
            )
            state.last_notified_at = now

        state.save(update_fields=['last_error', 'last_error_at', 'consecutive_failures', 'last_notified_at'])

    # --- drain: process up to `max_items` pending queue rows per tick ----

    def _drain(self, max_items: int):
        fetched = 0
        while fetched < max_items:
            row = SocialFetchQueueItem.objects.filter(status='pending', platform='pixiv').order_by('id').first()
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
                source='pixiv_bookmark',
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
            request = SimpleNamespace(data={'url': url}, query_params={})
            view = ItemViewSet()
            view.kwargs = {'pk': str(item.pk)}
            # get_object() (called inside fetch_and_save_preview) needs
            # self.request for its permission check -- see the identical
            # fix/comment in poll_twitter_updates.py's own _fetch_item.
            view.request = request
            response = view.fetch_and_save_preview(request, pk=item.pk)
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception('poll_pixiv_bookmarks: fetch failed for item %s (%s)', item.pk, url)
            return False
