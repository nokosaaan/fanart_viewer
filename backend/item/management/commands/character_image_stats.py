"""Read-only diagnostic: how many images exist per confirmed character in
this app's own DB. Answers the feasibility question for training a
supplementary character classifier on top of the existing tagger's frozen
features (see the 'evaluate_threshold' discussion) — a character needs
enough distinct images to generalize; this reports the actual distribution
instead of guessing.

Counts at the IMAGE level (an item's characters each get credited once per
image the item has — PreviewImage rows, or a single preview_data blob if no
PreviewImage rows exist), not just at the item level, since that's the real
unit a classifier would train on.

Never writes anything — pure read/report.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py character_image_stats
  docker compose -f docker-compose.prod.yml exec web python manage.py character_image_stats --min-images 15
"""
from collections import Counter

from django.core.management.base import BaseCommand

from item.models import Item


class Command(BaseCommand):
    help = 'Reports how many images exist per confirmed character — feasibility check for training a custom classifier.'

    def add_arguments(self, parser):
        parser.add_argument('--min-images', type=int, default=15,
                             help='Threshold (inclusive) used to report how many characters clear the bar '
                                  'for a trainable class (default 15 — a rough rule of thumb, not a hard rule)')
        parser.add_argument('--top', type=int, default=30,
                             help='How many of the best-covered characters to list by name (default 30)')

    def handle(self, *args, **options):
        min_images = options['min_images']
        top_n = options['top']

        counts = Counter()
        items = Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only('characters', 'preview_data', 'id')
        total_items = 0
        for item in items.iterator():
            n_images = item.preview_images.count()
            if n_images == 0:
                n_images = 1 if item.preview_data else 0
            if n_images == 0:
                continue
            total_items += 1
            for c in (item.characters or []):
                if c:
                    counts[c] += n_images

        if not counts:
            self.stdout.write(self.style.ERROR('No items with both confirmed characters and an image were found.'))
            return

        total_characters = len(counts)
        trainable = sum(1 for n in counts.values() if n >= min_images)
        buckets = [(1, 1), (2, 4), (5, 9), (10, 14), (15, 29), (30, 49), (50, None)]
        self.stdout.write(f'Items scanned (with characters + an image): {total_items}')
        self.stdout.write(f'Distinct characters: {total_characters}')
        self.stdout.write('')
        self.stdout.write('Images-per-character histogram:')
        for lo, hi in buckets:
            if hi is None:
                n = sum(1 for v in counts.values() if v >= lo)
                label = f'{lo}+'
            else:
                n = sum(1 for v in counts.values() if lo <= v <= hi)
                label = f'{lo}-{hi}'
            self.stdout.write(f'  {label:>8} images: {n:>5} characters')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{trainable}/{total_characters} characters have >= {min_images} images '
            f'(rough threshold for a trainable class).'
        ))
        self.stdout.write('')
        self.stdout.write(f'Top {top_n} best-covered characters:')
        for name, n in counts.most_common(top_n):
            self.stdout.write(f'  {n:>5}  {name}')
