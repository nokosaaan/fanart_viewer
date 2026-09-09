"""Continuously poll the logged-in Twitter/X account's bookmarks for new
tweets and archive them, throttled to a low, steady rate so this never
competes with interactive use of the web app.

Design (see the plan this implements, and item.twitter_gql_fetch's
fetch_account_retweets for the pattern this mirrors):

- Runs as its own long-lived process (a separate `poller` docker-compose
  service), never inside the `web` request/response cycle.
- Gated on PollerSettings (see item.models): does nothing at all unless
  `enabled` is set, and the delay between ticks is PollerSettings.
  interval_seconds — both re-read fresh from the DB after every single
  tick (not just once at process start), so a change made through the
  settings panel (TwitterCredsManager.jsx) takes effect on the very next
  cycle with no restart of this long-lived process needed. This is the
  docker `poller` service's equivalent of exe/launcher.py's own poller
  thread on the exe-packaged build, which re-reads the same field the
  same way for the same reason.
- One tick every PollerSettings.interval_seconds (default 360s = 6 min):
  1. discovery: pull the newest page of Bookmarks, stopping as soon as a
     tweet already known (already an Item, or already queued) is seen —
     so this only ever costs a couple of lightweight GraphQL calls per
     tick, not a full history re-scan. Likes are deliberately NOT polled
     automatically here — that needs resolving the logged-in account's own
     screen_name first (see twitter_gql_fetch.resolve_own_account), which
     depends on a correctly-formatted `twid` cookie and adds a whole extra
     failure mode to every single tick for a feature that isn't needed
     continuously. Likes are instead available as an on-demand "スキャンし
     て確認" action (see ItemViewSet.scan_account_likes/fetch_account_likes,
     item.views._run_account_likes_job, and LikeFetchManager.jsx) — the
     exact same manual-review pattern BookmarkFetchManager.jsx already uses
     for a bookmarks catch-up, just invoked by the user instead of on a
     timer.
  2. drain: pop up to PollerSettings.items_per_tick still-pending queue
     rows, NEWEST tweet first (ordered by external_id — Twitter's own
     snowflake id, monotonically increasing with creation time — not by
     when this app happened to discover it), and run each through the
     exact same fetch_and_save_preview flow a manual bookmark_fetch call
     uses. Rows already fetched manually in the meantime (or found already
     saved with a preview) are skipped — same "already_processed" check
     bookmark_fetch itself does — without counting against this budget,
     same as the manual browser-extension flow. Previously drained
     oldest-of-the-current-discovery-batch first, which meant a brand new
     bookmark could sit behind an entire backlog before ever being tried;
     with the frequency itself now user-configurable (see PollerSettings),
     there's no longer a reason to prefer clearing the backlog over
     surfacing what was JUST bookmarked.
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

from django.core.management.base import BaseCommand
from django.utils import timezone

from item.models import Item, PollerSettings, SocialFetchQueueItem, TwitterPollState
from item.notify import notify_discord
from item.twitter_creds import has_credentials
from item.twitter_gql_fetch import TwitterAuthError, TwitterGQLError, fetch_account_bookmarks
from item.views import (
    _call_fetch_and_save_preview, _find_item_by_url, _get_bookmarks_resume_cursor,
    _known_twitter_ids, _save_bookmarks_resume_cursor,
)

logger = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 360  # 6 min -> 10 ticks/hour -> 10 fetches/hour
MAX_PAGES_STEADY = 1
MAX_PAGES_BACKFILL = 3  # only used the very first time there's no history at all yet
NOTIFY_REPEAT_AFTER = timedelta(hours=24)


class Command(BaseCommand):
    help = (
        'Continuously poll Twitter/X bookmarks and likes for new items, '
        'archiving up to PollerSettings.items_per_tick per tick (default: '
        'every 6 minutes). No-ops entirely unless PollerSettings.enabled '
        'is set. Runs forever unless --once is passed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='Run a single tick then exit, instead of looping forever.',
        )

    def handle(self, *args, **options):
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception('poll_twitter_updates: unhandled error in tick')
            if options['once']:
                break
            # Re-read fresh every cycle (not cached in a local variable at
            # process start) — see the module docstring on why this makes
            # the settings panel's frequency control take effect without a
            # restart.
            try:
                interval = PollerSettings.objects.get(pk=1).interval_seconds
            except PollerSettings.DoesNotExist:
                interval = TICK_SECONDS_DEFAULT
            time.sleep(interval)

    # --- one tick = discovery + drain exactly one queued item -----------

    def _tick(self):
        if not has_credentials():
            logger.info('poll_twitter_updates: no Twitter credentials configured, skipping tick')
            return

        poller_settings, _ = PollerSettings.objects.get_or_create(pk=1)
        if not poller_settings.enabled:
            logger.info('poll_twitter_updates: disabled in settings, skipping tick')
            return

        state, _ = TwitterPollState.objects.get_or_create(pk=1)
        try:
            self._discover()
        except (TwitterAuthError, TwitterGQLError) as e:
            self._record_failure(state, str(e))
        except Exception as e:  # any other unexpected failure counts too
            logger.exception('poll_twitter_updates: discovery failed')
            self._record_failure(state, str(e))
        else:
            self._record_success(state)

        self._drain(poller_settings.items_per_tick)

    def _discover(self):
        # Shared with the manual scan_account_bookmarks_view/
        # scan_account_likes_view (see _known_twitter_ids's own docstring
        # for the full reasoning — a bare/preview-less Item or a 'failed'
        # queue row must NEVER count as "known", or discovery's own
        # stop-at-first-known-id logic permanently walls off every
        # genuinely older bookmark behind it the moment either happens
        # once). One shared implementation so this and the manual scans
        # can't silently drift apart on what "known" means.
        known_ids = _known_twitter_ids()

        # The backfill cap only matters the very first run (no history to
        # compare against yet, so a page full of new items wouldn't
        # otherwise stop); afterwards known_ids already bounds each fetch
        # to just what's new since the last tick.
        max_pages = MAX_PAGES_BACKFILL if not known_ids else MAX_PAGES_STEADY

        # Resume from wherever the previous tick left off if it never
        # reached a known tweet (still catching up on a backlog bigger
        # than one page) — see TwitterPollState.bookmarks_resume_cursor's
        # own docstring and _fetch_social_timeline's for the full
        # reasoning. Kept at the same max_pages either way: catching up
        # happens gradually, one tick's worth of pages at a time, never by
        # widening a single call's budget.
        #
        # Goes through the shared _get_bookmarks_resume_cursor/_save_
        # bookmarks_resume_cursor helpers (not a direct read/write of
        # `state.bookmarks_resume_cursor`) so the save is a compare-and-
        # swap against what was read here — a manual (non-full-scan)
        # bookmark catch-up shares this exact same field, and without this
        # a tick overlapping with one could silently lose whichever
        # progress got saved second (see _save_twitter_cursor_atomic's own
        # docstring for the full reasoning).
        start_cursor = _get_bookmarks_resume_cursor()
        bookmarks, bookmarks_resume = fetch_account_bookmarks(
            known_ids, max_pages=max_pages, start_cursor=start_cursor,
        )
        _save_bookmarks_resume_cursor(bookmarks_resume, expected_previous=start_cursor)

        queued_bookmarks = self._enqueue_new(bookmarks, 'bookmark')
        logger.info(
            'poll_twitter_updates: discovery found %d bookmark(s) (%d newly queued)',
            len(bookmarks), queued_bookmarks,
        )

    def _enqueue_new(self, candidates, kind):
        # candidates arrive newest-first (see fetch_account_bookmarks/
        # _fetch_social_timeline) — inserted in that same order. _drain
        # doesn't actually rely on insertion order to decide what's next
        # (it orders by external_id, i.e. tweet recency, not row id) but
        # keeping insertion order matching fetch order avoids surprises
        # for anything that inspects this table directly (e.g. the admin).
        queued = 0
        for cand in candidates:
            tweet_id = int(cand['tweet_id'])
            screen_name = cand.get('screen_name') or ''
            url = (
                f'https://x.com/{screen_name}/status/{tweet_id}' if screen_name
                else f'https://x.com/i/status/{tweet_id}'
            )

            existing = _find_item_by_url(url)
            if existing is not None and self._has_preview(existing):
                continue  # already fetched — nothing to do

            _row, created = SocialFetchQueueItem.objects.get_or_create(
                external_id=tweet_id,
                defaults={
                    'kind': kind, 'screen_name': screen_name, 'url': url,
                    'description': cand.get('description') or '',
                },
            )
            if created:
                queued += 1
        return queued

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

    # --- drain: process up to `max_items` pending queue rows per tick ----

    def _drain(self, max_items: int):
        fetched = 0
        skipped = 0
        failed = 0
        while fetched < max_items:
            # Newest tweet first (external_id is Twitter's own snowflake
            # id — monotonically increasing with creation time, so this
            # is a direct, durable "most recent first" ordering) — was
            # previously oldest-of-the-current-batch-first, which meant a
            # brand new bookmark could sit behind an entire backlog before
            # ever being processed. See the module docstring.
            #
            # 'pending' rows always take priority; 'failed' ones are
            # retried (never left permanently stuck — see _discover's own
            # comment on why a failed row must not count as "known"
            # either) only once there's no pending work left this tick, so
            # a backlog of retries can never crowd out brand new content.
            #
            # kind='bookmark' only — this command no longer discovers
            # Likes at all (see the module docstring), but a 'like' row
            # queued from before that change (or any other stray one)
            # must never be silently auto-fetched here either; Likes are
            # opt-in only, via LikeFetchManager.jsx's own manual scan,
            # which doesn't go through this queue at all.
            row = (
                SocialFetchQueueItem.objects.filter(status='pending', kind='bookmark').order_by('-external_id').first()
                or SocialFetchQueueItem.objects.filter(status='failed', kind='bookmark').order_by('-external_id').first()
            )
            if row is None:
                break

            existing = _find_item_by_url(row.url)
            if existing is not None and self._has_preview(existing):
                row.status = 'skipped'
                row.processed_at = timezone.now()
                row.save(update_fields=['status', 'processed_at'])
                skipped += 1
                continue  # doesn't count against this tick's fetch budget

            item = existing or Item.objects.create(
                external_id=row.external_id,
                source=f'twitter_{row.kind}',
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
            if not ok:
                failed += 1

        remaining_pending = SocialFetchQueueItem.objects.filter(status='pending', kind='bookmark').count()
        remaining_failed = SocialFetchQueueItem.objects.filter(status='failed', kind='bookmark').count()
        logger.info(
            'poll_twitter_updates: drain fetched %d (of which %d failed), skipped %d '
            'already-processed, %d still pending, %d still failed (retried next tick)',
            fetched, failed, skipped, remaining_pending, remaining_failed,
        )

    @staticmethod
    def _fetch_item(item: Item, url: str) -> bool:
        try:
            response = _call_fetch_and_save_preview(item.pk, {'url': url})
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception('poll_twitter_updates: fetch failed for item %s (%s)', item.pk, url)
            return False
