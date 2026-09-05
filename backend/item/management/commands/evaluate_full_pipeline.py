"""Like evaluate_tag_count, but reproduces the ENTIRE production suggestion
pipeline (hashtag match -> artist DB history -> tag similarity -> tagger
character recognition + CharacterGroup match -> optional Danbooru reverse
lookup -> OC fallback — see item.views._suggest_for_item) instead of testing
the tag-similarity matcher in isolation. Answers "how accurate is the real
thing, at various tagger tag counts" rather than "how accurate is just one
of its fallback layers".

Ground truth pool: same as evaluate_tag_count — items with titles,
characters, AND situation all confirmed, plus a preview image.

Method: each ground-truth item is cloned IN MEMORY ONLY (never saved — see
_make_blank_clone) with titles/characters/tags/situation cleared, so
_suggest_for_item sees it exactly as it would see a genuinely new,
just-fetched item. The tagger itself runs once per item (real inference,
~5s+); tagger.suggest_tags is then patched for the duration of that item's
per-N sweep to replay the same cached result instead of re-running
inference N times for nothing — the only thing that changes per N is how
many of the tagger's own (already-ranked) tags get truncated before
reaching the DB tag-similarity matcher (tag_limit_for_matching — a
test-only parameter that does not exist in the real request path; a real
request always passes None, i.e. unbounded, as it always has).

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_full_pipeline
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_full_pipeline --limit 100 --external
  # Compare tagger backends on the same ground truth (needs INSTALL_TIMM_TAGGER=1 for --model canary):
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_full_pipeline --model default
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_full_pipeline --model canary
"""
import copy
from unittest.mock import patch

from django.core.management.base import BaseCommand

from item import tagger
from item import views
from item.management.commands._eval_utils import get_evaluation_items


class Command(BaseCommand):
    help = (
        'Evaluates the FULL suggest_tags_view pipeline (not just tag similarity) '
        'at various tagger tag counts, against already-confirmed DB values.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                             help='Max number of ground-truth items to evaluate (default 50)')
        parser.add_argument('--seed', type=int, default=None,
                             help='Fix which ground-truth items are sampled, so a --model default run '
                                  'and a --model canary run compare the SAME items instead of two '
                                  'independent random subsets (default: random each run)')
        parser.add_argument('--output', type=str, default=None,
                             help='Output chart path (default: full_pipeline_evaluation_<model>.png)')
        parser.add_argument('--counts', type=str, default='3,5,8,10,12,15,20,25,30,40,50',
                             help='Comma-separated tag-count values to test')
        parser.add_argument('--external', action='store_true',
                             help='Also enable the Danbooru reverse-lookup step (external network '
                                  'calls) — off by default, matching the UI checkbox\'s default state')
        parser.add_argument('--use-classifier', action='store_true',
                             help="Also try item.tagger.predict_character (see train_character_classifier) "
                                  "at the same priority tier as the tagger's own direct character "
                                  "recognition — off by default (the live suggest_tags endpoint doesn't "
                                  "use it yet). Enable this to give the cascade the SAME signals "
                                  "evaluate_ensemble has, for a fair 'combination strategy' comparison.")
        parser.add_argument('--model', choices=['default', 'canary'], default='default',
                             help="Tagger backend to run: 'default' (small ONNX model, always available) "
                                  "or 'canary' (the larger, more recently-trained 'timm' backend — see "
                                  "tagger.py's TIMM_MODEL_REPO/HAVE_TIMM; needs INSTALL_TIMM_TAGGER=1 at "
                                  "build time). Same choice values as the UI's model dropdown.")

    def handle(self, *args, **options):
        import matplotlib
        matplotlib.use('Agg')  # headless — this runs in a container with no display
        import matplotlib.pyplot as plt

        counts = sorted({int(x) for x in options['counts'].split(',') if x.strip()})
        limit = options['limit']
        seed = options['seed']
        external = options['external']
        use_classifier = options['use_classifier']
        model_choice = options['model']
        tagger_backend = 'timm' if model_choice == 'canary' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            self.stderr.write(self.style.ERROR(
                "--model canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            ))
            return
        output_path = options['output'] or f'full_pipeline_evaluation_{model_choice}.png'

        candidates = get_evaluation_items(limit, seed)

        evaluated = []  # (real_item, image_bytes) — real_item's own fields are never touched
        for item in candidates:
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                continue  # no image to run the tagger on — can't evaluate this one
            evaluated.append((item, image_bytes))

        if not evaluated:
            self.stderr.write(self.style.ERROR(
                'No evaluable items found — need titles+characters+situation all set, plus an image.'
            ))
            return

        self.stdout.write(
            f'Evaluating {len(evaluated)} items across tag counts: {counts} '
            f'(model={model_choice}, external={external}, seed={seed})'
        )

        hits = {n: {'title': 0, 'char': 0, 'situation': 0} for n in counts}
        for i, (item, image_bytes) in enumerate(evaluated):
            try:
                # Real inference, ONCE per item — general_limit only bounds
                # the displayed `tags` field, irrelevant to matching either
                # in production or here (see _suggest_for_item's docstring).
                cached_tagger_result = tagger.suggest_tags(image_bytes, general_limit=9999, backend=tagger_backend)
            except Exception as e:
                self.stderr.write(f'Item {item.id}: tagger failed ({e}), skipping')
                continue

            for n in counts:
                # In-memory clone only — .save() is never called on this,
                # so the real DB row (with its real confirmed values) is
                # completely untouched. Same pk, so _suggest_from_existing_data
                # / _suggest_from_similar_tags's exclude_pk=item.pk still
                # correctly excludes this item's own real row from being
                # used as its own "sibling" evidence.
                blank = copy.copy(item)
                blank.titles = []
                blank.characters = []
                blank.tags = None
                blank.situation = ''

                with patch.object(tagger, 'suggest_tags', return_value=cached_tagger_result):
                    result = views._suggest_for_item(blank, external=external, tag_limit_for_matching=n,
                                                      use_classifier=use_classifier)

                if set(result['suggested_titles']) & set(item.titles or []):
                    hits[n]['title'] += 1
                if {c['name'] for c in result['characters']} & set(item.characters or []):
                    hits[n]['char'] += 1
                if result['situation_hint'] and result['situation_hint'] == item.situation:
                    hits[n]['situation'] += 1

            self.stdout.write(f'Evaluated item {item.id} ({i + 1}/{len(evaluated)})')

        total = len(evaluated)
        title_rates, char_rates, situation_rates = [], [], []
        for n in counts:
            title_rates.append(hits[n]['title'] / total * 100)
            char_rates.append(hits[n]['char'] / total * 100)
            situation_rates.append(hits[n]['situation'] / total * 100)
            self.stdout.write(
                f"N={n:>3}: title={hits[n]['title']}/{total} ({title_rates[-1]:5.1f}%)  "
                f"char={hits[n]['char']}/{total} ({char_rates[-1]:5.1f}%)  "
                f"situation={hits[n]['situation']}/{total} ({situation_rates[-1]:5.1f}%)"
            )

        plt.figure(figsize=(8, 5))
        plt.plot(counts, title_rates, marker='o', label='Title')
        plt.plot(counts, char_rates, marker='o', label='Character')
        plt.plot(counts, situation_rates, marker='o', label='Situation')
        plt.axvline(15, color='gray', linestyle='--', alpha=0.5, label='current cap (15)')
        plt.xlabel('Tag count fed to matching (N)')
        plt.ylabel('Match rate (%)')
        seed_label = f', seed={seed}' if seed is not None else ''
        plt.title(
            f'Full pipeline: tag count vs match rate '
            f'(model={model_choice}, n={total} items, external={external}{seed_label})'
        )
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.ylim(0, 100)
        plt.tight_layout()
        plt.savefig(output_path)
        self.stdout.write(self.style.SUCCESS(f"Chart saved to {output_path}"))
