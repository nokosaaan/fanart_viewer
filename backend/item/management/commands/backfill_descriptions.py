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
keeps a 672-item backlog cheap and fast to backfill, and — just as
important — safe: gallery-dl/twitter_gql/yt-dlp all share the same
per-account GraphQL rate-limit bucket that other features in this app
(RetweetFetchManager, poll_twitter_updates) already have to budget
carefully around (see twitter_gql_fetch.py's own comments on this), so
--limit defaults to a conservative batch size rather than trying to walk
the whole backlog in one run.

Usage:
  docker compose exec web python manage.py backfill_descriptions
  docker compose exec web python manage.py backfill_descriptions --limit 50
  docker compose exec web python manage.py backfill_descriptions --dry-run
  docker compose exec web python manage.py backfill_descriptions --limit 0  # no cap — process everything found
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

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

        queryset = Item.objects.filter(description='').filter(
            Q(link__icontains='twitter.com') | Q(link__icontains='x.com')
        ).exclude(link='').order_by('id')

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
                if not description:
                    skipped += 1
                else:
                    updated += 1
                    if not options['dry_run']:
                        item.description = description
                        item.save(update_fields=['description'])

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
                '(already-updated items no longer match the empty-description filter).'
            )
