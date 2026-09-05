"""Continuously poll the logged-in Twitter/X account's bookmarks and likes
for new tweets and archive them, throttled to a low, steady rate so this
never competes with interactive use of the web app.

Design (see the plan this implements, and item.twitter_gql_fetch's
fetch_account_retweets for the pattern this mirrors):

- Runs as its own long-lived process (a separate `poller` docker-compose
  service), never inside the `web` request/response cycle.
- One tick every `--tick-seconds` (default 360s = 6 min -> 10 ticks/hour):
  1. discovery: pull the newest page of Bookmarks and of Likes, stopping as
     soon as a tweet already known (already an Item, or already queued) is
     seen — so this only ever costs a couple of lightweight GraphQL calls
     per tick, not a full history re-scan.
  2. drain: pop the single oldest still-pending queue row and run it
     through the exact same fetch_and_save_preview flow a manual
     bookmark_fetch call uses. Rows already fetched manually in the
     meantime are skipped without counting against this tick's one fetch.
- Twitter fetches for a twitter.com/x.com URL go through gallery-dl/
  twitter_gql/yt-dlp (plain HTTP, no headless browser) well before any
  Playwright fallback, which is only ever triggered by an explicit
  force_method request — so this command never spins up a browser.
- Auth/query-id failures during discovery update TwitterPollState and
  fire a (rate-limited) Discord notification via item.notify — see
  _record_failure. Failures fetching one individual queued tweet do not
  notify (same as a manual fetch failing; not necessarily an auth issue).
"""
import logging
import time
from datetime import timedelta
from types import SimpleNamespace

from django.core.management.base import BaseCommand
from django.utils import timezone

from item.models import Item, SocialFetchQueueItem, TwitterPollState
from item.notify import notify_discord
from item.twitter_creds import has_credentials
from item.twitter_gql_fetch import (
    TwitterAuthError,
    TwitterGQLError,
    fetch_account_bookmarks,
    fetch_account_likes,
    verify_credentials,
)
from item.views import ItemViewSet, _find_item_by_url

logger = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 360  # 6 min -> 10 ticks/hour -> 10 fetches/hour
MAX_PAGES_STEADY = 1
MAX_PAGES_BACKFILL = 3  # only used the very first time there's no history at all yet
NOTIFY_REPEAT_AFTER = timedelta(hours=24)

# source strings on Item.source for tweets archived via this poller (and,
# for 'twitter_bookmark', via the browser-extension bookmark_fetch path too)
_KNOWN_TWITTER_SOURCES = ['twitter_bookmark', 'twitter_like', 'twitter_rt']


class Command(BaseCommand):
    help = (
        'Continuously poll Twitter/X bookmarks and likes for new items, '
        'archiving at most one per tick (default: every 6 minutes, i.e. '
        '10/hour). Runs forever unless --once is passed.'
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
                logger.exception('poll_twitter_updates: unhandled error in tick')
            if options['once']:
                break
            time.sleep(tick_seconds)

    # --- one tick = discovery + drain exactly one queued item -----------

    def _tick(self):
        if not has_credentials():
            logger.info('poll_twitter_updates: no Twitter credentials configured, skipping tick')
            return

        state, _ = TwitterPollState.objects.get_or_create(pk=1)
        try:
            self._discover(state)
        except (TwitterAuthError, TwitterGQLError) as e:
            self._record_failure(state, str(e))
        except Exception as e:  # any other unexpected failure counts too
            logger.exception('poll_twitter_updates: discovery failed')
            self._record_failure(state, str(e))
        else:
            self._record_success(state)

        self._drain_one()

    def _discover(self, state: TwitterPollState):
        if not state.screen_name:
            result = verify_credentials()
            if not result.get('ok'):
                raise TwitterAuthError(result.get('reason') or 'verify_credentials failed')
            state.screen_name = result.get('screen_name') or ''
            state.save(update_fields=['screen_name'])

        known_ids = set(
            Item.objects.filter(source__in=_KNOWN_TWITTER_SOURCES)
            .values_list('external_id', flat=True)
        )
        known_ids |= set(SocialFetchQueueItem.objects.values_list('external_id', flat=True))

        # The backfill cap only matters the very first run (no history to
        # compare against yet, so a page full of new items wouldn't
        # otherwise stop); afterwards known_ids already bounds each fetch
        # to just what's new since the last tick.
        max_pages = MAX_PAGES_BACKFILL if not known_ids else MAX_PAGES_STEADY

        bookmarks = fetch_account_bookmarks(known_ids, max_pages=max_pages)
        likes = (
            fetch_account_likes(state.screen_name, known_ids, max_pages=max_pages)
            if state.screen_name else []
        )

        self._enqueue_new(bookmarks, 'bookmark')
        self._enqueue_new(likes, 'like')

    def _enqueue_new(self, candidates, kind):
        # candidates arrive newest-first; reverse so the oldest-in-this-
        # batch is inserted (and therefore dequeued) first.
        for cand in reversed(candidates):
            tweet_id = int(cand['tweet_id'])
            screen_name = cand.get('screen_name') or ''
            url = (
                f'https://x.com/{screen_name}/status/{tweet_id}' if screen_name
                else f'https://x.com/i/status/{tweet_id}'
            )

            existing = _find_item_by_url(url)
            if existing is not None and self._has_preview(existing):
                continue  # already fetched — nothing to do

            SocialFetchQueueItem.objects.get_or_create(
                external_id=tweet_id,
                defaults={'kind': kind, 'screen_name': screen_name, 'url': url},
            )

    @staticmethod
    def _has_preview(item: Item) -> bool:
        try:
            return item.preview_images.exists() or bool(item.preview_data)
        except Exception:
            return False

    def _record_success(self, state: TwitterPollState):
        state.last_success_at = timezone.now()
        state.consecutive_failures = 0
        state.save(update_fields=['last_success_at', 'consecutive_failures'])

    def _record_failure(self, state: TwitterPollState, message: str):
        now = timezone.now()
        was_already_failing = state.consecutive_failures > 0
        # A cached screen_name won't get re-resolved after a real auth
        # failure otherwise (it's only looked up when blank) — clear it so
        # the next tick re-verifies credentials instead of reusing a
        # possibly-stale value tied to the now-invalid session.
        state.screen_name = ''
        state.last_error = message
        state.last_error_at = now
        state.consecutive_failures += 1

        should_notify = (
            not was_already_failing
            or state.last_notified_at is None
            or (now - state.last_notified_at) >= NOTIFY_REPEAT_AFTER
        )
        if should_notify:
            notify_discord(
                ':warning: fanart_viewer: Twitterのブックマーク/いいね自動取得が失敗しています。\n'
                f'{message}\n'
                'auth_token/ct0 が期限切れの可能性があります。管理画面から再設定してください。'
            )
            state.last_notified_at = now

        state.save(update_fields=[
            'screen_name', 'last_error', 'last_error_at',
            'consecutive_failures', 'last_notified_at',
        ])

    # --- drain: process exactly one pending queue row per tick -----------

    def _drain_one(self):
        while True:
            row = SocialFetchQueueItem.objects.filter(status='pending').order_by('id').first()
            if row is None:
                return

            existing = _find_item_by_url(row.url)
            if existing is not None and self._has_preview(existing):
                row.status = 'skipped'
                row.processed_at = timezone.now()
                row.save(update_fields=['status', 'processed_at'])
                continue  # doesn't count against this tick's one live fetch

            item = existing or Item.objects.create(
                external_id=row.external_id,
                source=f'twitter_{row.kind}',
                situation='',
                titles=[],
                characters=[],
                artist=row.screen_name,
                link=row.url,
                tags=None,
            )

            ok = self._fetch_item(item, row.url)

            row.status = 'done' if ok else 'failed'
            row.processed_at = timezone.now()
            row.save(update_fields=['status', 'processed_at'])
            return  # exactly one live fetch attempt per tick

    @staticmethod
    def _fetch_item(item: Item, url: str) -> bool:
        try:
            request = SimpleNamespace(data={'url': url}, query_params={})
            view = ItemViewSet()
            view.kwargs = {'pk': str(item.pk)}
            response = view.fetch_and_save_preview(request, pk=item.pk)
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception('poll_twitter_updates: fetch failed for item %s (%s)', item.pk, url)
            return False
