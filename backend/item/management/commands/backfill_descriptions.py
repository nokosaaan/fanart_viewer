"""Backfills Item.description for existing Twitter/X items that predate the
description-capture fix in ItemViewSet.fetch_and_save_preview (see that
view's "Post text top-up" comment): items whose image was already fetched
via the plain HTML/og:image scrape never got their post text captured,
since that path — unlike gallery-dl/twitter_gql/yt-dlp — never returns one.
The fix only applies going forward (the next time fetch_and_save_preview
runs for an item); it does nothing for items already sitting in the DB with
an empty description. This command is the one-time catch-up for those.

Deliberately does NOT re-fetch or re-download any images — it only calls
item.twitter_gql_fetch.fetch_tweet_description(), which resolves the
tweet's text via a single lightweight TweetDetail GraphQL call and never
touches pbs.twimg.com at all (see that function's own docstring). This
keeps a large backlog cheap and fast to backfill, and — just as
important — safe: gallery-dl/twitter_gql/yt-dlp all share the same
per-account GraphQL rate-limit bucket that other features in this app
(RetweetFetchManager, poll_twitter_updates) already have to budget
carefully around (see twitter_gql_fetch.py's own comments on this), so
--limit defaults to a conservative batch size rather than trying to walk
the whole backlog in one run. fetch_tweet_media_urls (which
fetch_tweet_description calls into) also goes through the same
rate-limit-aware backoff helper (_get_with_ratelimit_backoff) the
account-scan features already use, so a run that gets close to the limit
slows down/waits instead of just running into a hard 429.

Processes newest items first (order_by('-id')) — recently-fetched items are
both more likely to still be reachable (an old tweet is more likely to have
been deleted/gone private since) and more relevant to what's actively being
reviewed in the edit queue right now.

Every item this command reaches gets stamped with description_checked_at
once it gets a definitive answer (text found, or confirmed there isn't
any) — see that field's own docstring in models.py. A re-run's queryset
excludes already-checked items entirely, so repeated runs never re-query
Twitter for the same "no text" items over and over; only items that
errored out (network/auth failure) or haven't been attempted yet remain.

Usage:
  docker compose exec web python manage.py backfill_descriptions
  docker compose exec web python manage.py backfill_descriptions --limit 50
  docker compose exec web python manage.py backfill_descriptions --dry-run
  docker compose exec web python manage.py backfill_descriptions --limit 0  # no cap — process everything found
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from item.models import Item
from item.twitter_creds import has_credentials
from item.twitter_gql_fetch import TwitterAuthError, TwitterGQLError, fetch_tweet_description

DEFAULT_LIMIT = 30
DEFAULT_SLEEP_SECONDS = 1.5


class Command(BaseCommand):
    help = (
        'Backfills Item.description for existing Twitter/X items that have '
        'an image but no captured post text (see fetch_and_save_preview\'s '
        'description top-up fix, which only applies to future fetches). '
        'Text-only — never re-downloads images.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=DEFAULT_LIMIT,
            help=f'Max items to process this run (default: {DEFAULT_LIMIT} — a conservative batch size; '
                 'pass 0 to process every matching item found, if you\'re confident about rate limits).',
        )
        parser.add_argument(
            '--sleep', type=float, default=DEFAULT_SLEEP_SECONDS,
            help=f'Seconds to wait between requests (default: {DEFAULT_SLEEP_SECONDS}).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be updated without actually saving anything.',
        )

    def handle(self, *args, **options):
        if not has_credentials():
            self.stderr.write(self.style.ERROR(
                'No Twitter credentials configured (TWITTER_AUTH_TOKEN/TWITTER_CT0 env vars, or the '
                'admin UI\'s Twitter/X 認証情報 panel) — cannot call the TweetDetail API.'
            ))
            return

        # description_checked_at__isnull=True excludes items a previous run
        # already confirmed have no text to find (see that field's own
        # docstring) — only items never yet successfully checked, or that
        # failed last time (worth retrying), are considered.
        queryset = Item.objects.filter(description='', description_checked_at__isnull=True).filter(
            Q(link__icontains='twitter.com') | Q(link__icontains='x.com')
        ).exclude(link='').order_by('-id')

        limit = options['limit']
        total_matching = queryset.count()
        if limit and limit > 0:
            queryset = queryset[:limit]

        items = list(queryset)
        self.stdout.write(
            f'{total_matching} item(s) match (empty description, Twitter/X link) — '
            f'processing {len(items)} this run.'
        )
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: no changes will be saved.'))

        updated = skipped = failed = 0
        for i, item in enumerate(items, start=1):
            try:
                description = fetch_tweet_description(item.link)
            except TwitterAuthError as e:
                self.stderr.write(self.style.ERROR(
                    f'Auth error on item {item.id} — stopping early (auth_token/ct0 likely expired): {e}'
                ))
                break
            except TwitterGQLError as e:
                self.stderr.write(f'item {item.id}: {e}, skipping')
                failed += 1
            except Exception as e:
                self.stderr.write(f'item {item.id}: unexpected error ({e}), skipping')
                failed += 1
            else:
                # Either way, we now have a confirmed answer for this item —
                # stamp description_checked_at so a re-run's queryset leaves
                # it alone from here on (see that field's own docstring).
                # Only a raised exception above (network/auth/GraphQL error)
                # skips this — those should be retried, not remembered as
                # "checked".
                if not description:
                    skipped += 1
                    if not options['dry_run']:
                        item.description_checked_at = timezone.now()
                        item.save(update_fields=['description_checked_at'])
                else:
                    updated += 1
                    if not options['dry_run']:
                        item.description = description
                        item.description_checked_at = timezone.now()
                        item.save(update_fields=['description', 'description_checked_at'])

            if i % 10 == 0 or i == len(items):
                self.stdout.write(f'{i}/{len(items)} processed (updated={updated} skipped={skipped} failed={failed})')
            if i < len(items):
                time.sleep(options['sleep'])

        remaining = max(0, total_matching - len(items))
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. updated={updated} skipped={skipped} failed={failed}'
            + (' (dry-run, nothing saved)' if options['dry_run'] else '')
        ))
        if remaining:
            self.stdout.write(
                f'{remaining} more matching item(s) left — re-run the same command to continue '
                '(updated and skipped items are both stamped with description_checked_at, so a '
                're-run never re-queries either of them — only failed/not-yet-attempted items remain).'
            )
