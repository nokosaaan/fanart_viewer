"""Evaluates how the number of tags fed into the DB-similarity suggestion
matcher (_suggest_from_similar_tags, see item/views.py) affects how often it
correctly names the title/character/situation this app already has
confirmed for that same item — i.e. whether the current general_limit=15
cap (see tagger.py) is actually the right tradeoff, or whether more/fewer
tags would do better/worse.

Ground truth pool: items that already have titles, characters, AND
situation all confirmed (so there's something to check predictions
against), and at least one preview image (so the tagger has something to
re-run on). For each such item, the tagger runs ONCE with an unbounded
general_limit to get its full confidence-ranked tag list; each tested N is
then just a truncation of that same ranked list, not a separate re-run —
re-running the tagger per N would be both wasteful and pointless (the
tagger's own ranking doesn't change; only how much of it gets cut off).

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_tag_count
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_tag_count --limit 100 --counts 5,10,15,20,30
"""
from django.core.management.base import BaseCommand

from item.models import Item
from item import tagger
from item.views import _suggest_from_similar_tags


class Command(BaseCommand):
    help = (
        'Evaluates tag-count vs title/character/situation match rate against '
        'already-confirmed DB values, and plots the result.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                             help='Max number of ground-truth items to evaluate (default 50)')
        parser.add_argument('--output', type=str, default='tag_count_evaluation.png',
                             help='Output chart path (default tag_count_evaluation.png)')
        parser.add_argument('--counts', type=str, default='3,5,8,10,12,15,20,25,30,40,50',
                             help='Comma-separated tag-count values to test')

    def handle(self, *args, **options):
        import matplotlib
        matplotlib.use('Agg')  # headless — this runs in a container with no display
        import matplotlib.pyplot as plt

        counts = sorted({int(x) for x in options['counts'].split(',') if x.strip()})
        limit = options['limit']

        # Ground truth pool: fully-confirmed items only, so there's a real
        # answer to check predictions against on all three fronts.
        candidates = (
            Item.objects
            .exclude(titles=[]).exclude(titles__isnull=True)
            .exclude(characters=[]).exclude(characters__isnull=True)
            .exclude(situation='').exclude(situation__isnull=True)
            .order_by('?')[:limit]
        )

        evaluated = []  # (item, tags_full) — tags_full is the tagger's full confidence-ranked list
        for item in candidates:
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                continue  # no image to re-run the tagger on — can't evaluate this one

            try:
                # general_limit only bounds the returned `tags` field, not
                # `tags_full` (already uncapped) — passed high anyway for clarity.
                result = tagger.suggest_tags(image_bytes, general_limit=9999, backend='onnx')
            except Exception as e:
                self.stderr.write(f'Item {item.id}: tagger failed ({e}), skipping')
                continue

            evaluated.append((item, result['tags_full']))
            self.stdout.write(f'Tagged item {item.id} ({len(evaluated)}/{limit} so far)')

        if not evaluated:
            self.stderr.write(self.style.ERROR(
                'No evaluable items found — need titles+characters+situation all set, plus an image.'
            ))
            return

        self.stdout.write(f'Evaluating {len(evaluated)} items across tag counts: {counts}')

        title_rates, char_rates, situation_rates = [], [], []
        for n in counts:
            title_hits = char_hits = situation_hits = 0
            for item, tags_full in evaluated:
                sim = _suggest_from_similar_tags(tags_full[:n], item.description, exclude_pk=item.pk)
                if set(sim['titles']) & set(item.titles or []):
                    title_hits += 1
                if set(sim['characters']) & set(item.characters or []):
                    char_hits += 1
                if sim['situation_hint'] and sim['situation_hint'] == item.situation:
                    situation_hits += 1

            total = len(evaluated)
            title_rates.append(title_hits / total * 100)
            char_rates.append(char_hits / total * 100)
            situation_rates.append(situation_hits / total * 100)
            self.stdout.write(
                f'N={n:>3}: title={title_hits}/{total} ({title_rates[-1]:5.1f}%)  '
                f'char={char_hits}/{total} ({char_rates[-1]:5.1f}%)  '
                f'situation={situation_hits}/{total} ({situation_rates[-1]:5.1f}%)'
            )

        plt.figure(figsize=(8, 5))
        plt.plot(counts, title_rates, marker='o', label='Title')
        plt.plot(counts, char_rates, marker='o', label='Character')
        plt.plot(counts, situation_rates, marker='o', label='Situation')
        plt.axvline(15, color='gray', linestyle='--', alpha=0.5, label='current cap (15)')
        plt.xlabel('Tag count (N)')
        plt.ylabel('Match rate (%)')
        plt.title(f'Tag count vs DB-similarity match rate (n={len(evaluated)} items)')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.ylim(0, 100)
        plt.tight_layout()
        plt.savefig(options['output'])
        self.stdout.write(self.style.SUCCESS(f"Chart saved to {options['output']}"))
