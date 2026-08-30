"""Evaluates the tagger's OWN character recognition (never mind the DB
tag-similarity fallback or Danbooru reverse lookup — see _suggest_for_item)
as a function of character_threshold, for a fixed backend. Orthogonal to
evaluate_tag_count/evaluate_full_pipeline, which vary how many tags get fed
to the DB matcher while always using the tagger's default
character_threshold=0.85. This instead asks: is 0.85 actually the right
cutoff for THIS backend's confidence distribution? A differently-trained
model (e.g. the 'timm'/canary backend) can have a differently calibrated
confidence scale, so a threshold tuned against the default ONNX model may
not transfer as-is.

Reports both:
  - recall (item-level): of the evaluated items, how many had at least one
    of their confirmed characters actually named at this threshold.
  - precision (tag-level): of everything the tagger named at this
    threshold across all items, how much of it was actually correct — a
    threshold that just floods low-confidence guesses could look good on
    recall alone without this.

Threshold sweep is cheap: tagger.suggest_tags_multi_threshold runs
inference (whole image + any person-detection crops) only ONCE per item,
then re-applies each threshold value to the same cached per-tag
probabilities — the forward pass doesn't change with a different
threshold, only which already-computed tags pass the cutoff.

Ground truth pool and --seed behave exactly like evaluate_tag_count/
evaluate_full_pipeline (see _eval_utils.get_evaluation_items) — fix --seed
to compare --model default vs --model canary on the SAME items.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_threshold --seed 42
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_threshold --seed 42 --model canary
"""
from django.core.management.base import BaseCommand

from item import tagger
from item.views import _normalize_char_name
from item.management.commands._eval_utils import get_evaluation_items


class Command(BaseCommand):
    help = (
        "Evaluates the tagger's character_threshold vs precision/recall against "
        'already-confirmed DB characters, and plots the result.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                             help='Max number of ground-truth items to evaluate (default 50)')
        parser.add_argument('--seed', type=int, default=None,
                             help='Fix which ground-truth items are sampled, so a --model default run '
                                  'and a --model canary run compare the SAME items instead of two '
                                  'independent random subsets (default: random each run)')
        parser.add_argument('--output', type=str, default=None,
                             help='Output chart path (default: threshold_evaluation_<model>.png)')
        parser.add_argument('--thresholds', type=str,
                             default='0.3,0.4,0.5,0.6,0.65,0.7,0.75,0.8,0.85,0.9,0.95',
                             help='Comma-separated character_threshold values to test')
        parser.add_argument('--model', choices=['default', 'canary'], default='default',
                             help="Tagger backend to run: 'default' (small ONNX model, always available) "
                                  "or 'canary' (the larger 'timm' backend — needs INSTALL_TIMM_TAGGER=1 "
                                  "at build time). Same choice values as the UI's model dropdown.")

    def handle(self, *args, **options):
        import matplotlib
        matplotlib.use('Agg')  # headless — this runs in a container with no display
        import matplotlib.pyplot as plt

        thresholds = sorted({float(x) for x in options['thresholds'].split(',') if x.strip()})
        limit = options['limit']
        seed = options['seed']
        model_choice = options['model']
        tagger_backend = 'timm' if model_choice == 'canary' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            self.stderr.write(self.style.ERROR(
                "--model canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            ))
            return
        output_path = options['output'] or f'threshold_evaluation_{model_choice}.png'

        items = get_evaluation_items(limit, seed)
        if not items:
            self.stderr.write(self.style.ERROR(
                'No evaluable items found — need titles+characters+situation all set, plus an image.'
            ))
            return

        self.stdout.write(
            f'Evaluating {len(items)} items across thresholds: {thresholds} '
            f'(model={model_choice}, seed={seed})'
        )

        stats = {t: {'tp': 0, 'fp': 0, 'total_items': 0, 'items_with_hit': 0} for t in thresholds}

        for i, item in enumerate(items):
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                continue  # no image to run the tagger on — can't evaluate this one

            try:
                per_threshold = tagger.suggest_tags_multi_threshold(
                    image_bytes, thresholds, backend=tagger_backend,
                )
            except Exception as e:
                self.stderr.write(f'Item {item.id}: tagger failed ({e}), skipping')
                continue

            # Compare on the SAME normalized spelling _match_tagger_characters
            # (views.py) uses in production — the tagger emits Danbooru-style
            # names ("hakurei reimu"), while this app's own DB rows keep
            # whatever casing/spacing/underscore convention was typed in
            # ("Hakurei Reimu"). Comparing raw strings would call almost
            # every correct match a miss, regardless of threshold.
            true_characters = {_normalize_char_name(c) for c in (item.characters or [])}
            for t in thresholds:
                predicted = {_normalize_char_name(c['name']) for c in per_threshold[t]['characters']}
                tp = predicted & true_characters
                fp = predicted - true_characters
                stats[t]['tp'] += len(tp)
                stats[t]['fp'] += len(fp)
                stats[t]['total_items'] += 1
                if tp:
                    stats[t]['items_with_hit'] += 1

            self.stdout.write(f'Evaluated item {item.id} ({i + 1}/{len(items)})')

        recall_rates, precision_rates = [], []
        for t in thresholds:
            s = stats[t]
            recall = s['items_with_hit'] / s['total_items'] * 100 if s['total_items'] else 0
            precision = s['tp'] / (s['tp'] + s['fp']) * 100 if (s['tp'] + s['fp']) else None
            recall_rates.append(recall)
            precision_rates.append(precision if precision is not None else float('nan'))
            self.stdout.write(
                f"threshold={t:.2f}: recall(item-level)={recall:5.1f}%  "
                f"precision(tag-level)={'n/a' if precision is None else f'{precision:5.1f}%'}  "
                f"(tp={s['tp']}, fp={s['fp']})"
            )

        plt.figure(figsize=(8, 5))
        plt.plot(thresholds, recall_rates, marker='o', label='Recall (item has a correct character named)')
        plt.plot(thresholds, precision_rates, marker='o', label='Precision (named characters that are correct)')
        plt.axvline(0.85, color='gray', linestyle='--', alpha=0.5, label='current default (0.85)')
        plt.xlabel('character_threshold')
        plt.ylabel('Rate (%)')
        seed_label = f', seed={seed}' if seed is not None else ''
        plt.title(f'Character threshold vs precision/recall (model={model_choice}, n={len(items)} items{seed_label})')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.ylim(0, 100)
        plt.tight_layout()
        plt.savefig(output_path)
        self.stdout.write(self.style.SUCCESS(f"Chart saved to {output_path}"))
