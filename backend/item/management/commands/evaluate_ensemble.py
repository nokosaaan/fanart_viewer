"""Evaluates the WEIGHTED-ENSEMBLE alternative to the live suggestion
pipeline's "first source to resolve a field wins" cascade (see
item.views._suggest_for_item) — a redesign motivated by a real structural
flaw: a weak DB signal (e.g. an artist's history that barely clears its own
confidence bar) currently gets tried early and, if it resolves a field at
all, permanently blocks stronger, more specific signals (e.g. the tagger
directly recognizing a character in THIS image) from ever running, since
the cascade never gets that far.

_collect_candidates (views.py) runs every applicable source unconditionally
instead and returns ALL of their candidates, each tagged with source +
confidence. _combine_candidates then sums weights[source] * confidence per
unique candidate value (so multiple sources agreeing reinforces the
answer) and returns the top-scoring one. This command evaluates that
combiner against the SAME confirmed-DB ground truth/blank-clone methodology
evaluate_full_pipeline uses, either for one weights vector (--weights) or
swept over a grid (--grid-search) — to empirically tune the weights rather
than guess them.

Cost note: inference/DB-lookup (the expensive part) runs EXACTLY ONCE per
item, cached, regardless of how many weight combinations get tried
afterward — only the final combination step (pure arithmetic over the
cached candidate lists) is repeated per weight vector, which is cheap
enough to grid-search thousands of combinations in seconds.

`--weak-factor` (default 0.5) derives artist_history_weak/tag_similarity_weak
from their strong counterparts' weight rather than sweeping them as
separate grid dimensions — keeps the grid at 6 tunable dimensions
(hashtag, artist_history, tag_similarity, tagger, danbooru, classifier)
instead of 9, which is what actually makes exhaustive grid search
tractable here. tagger_group (title inferred via a CharacterGroup match)
always shares the 'tagger' weight — same underlying recognition, just
resolved to a title instead of a character name.

'classifier' is item.tagger.predict_character's own supplementary
character classifier (see train_character_classifier) — only contributes a
candidate for single-subject images with a trained classifier available
for the chosen --model; on an item where it has nothing to say (no
classifier trained yet, or the image has 2+ people, or its own confidence
is below its threshold) it simply contributes no candidate, so the other
sources (artist_history/tag_similarity in particular) still cover it —
this is a deliberate design choice over hard-deleting those sources'
character output: a weighted addition preserves full coverage while still
letting the classifier dominate the combined score wherever it is
confident, rather than trading away everything it hasn't been trained on.

Usage:
  # Score the current hand-tuned defaults:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_ensemble --seed 42

  # Try one specific weights vector:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_ensemble --seed 42 \\
      --weights '{"hashtag": 5, "artist_history": 1, "tag_similarity": 3, "tagger": 4, "danbooru": 3}'

  # Grid-search for the best combination:
  docker compose -f docker-compose.prod.yml exec web python manage.py evaluate_ensemble --seed 42 --grid-search
"""
import copy
import itertools
import json

from django.core.management.base import BaseCommand

from item import tagger as tagger_module
from item import views
from item.management.commands._eval_utils import get_evaluation_items


class Command(BaseCommand):
    help = (
        'Evaluates the weighted-ensemble suggestion combiner (item.views._collect_candidates + '
        '_combine_candidates) against confirmed DB values — for a single weights vector or a grid search.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                             help='Max number of ground-truth items to evaluate (default 50)')
        parser.add_argument('--seed', type=int, default=None,
                             help='Fix which ground-truth items are sampled (default: random each run)')
        parser.add_argument('--model', choices=['default', 'canary'], default='default',
                             help="Tagger backend for the candidate-collection phase")
        parser.add_argument('--external', action='store_true',
                             help='Also run the Danbooru reverse-lookup step during candidate collection')
        parser.add_argument('--weights', type=str, default=None,
                             help='JSON dict of source->weight for a SINGLE run, e.g. '
                                  '\'{"hashtag": 5, "tagger": 4}\' — unspecified sources fall back to '
                                  'DEFAULT_ENSEMBLE_WEIGHTS. Overrides --grid-search when given.')
        parser.add_argument('--grid-search', action='store_true',
                             help='Sweep --grid-values over the 6 main weight dimensions instead of '
                                  'scoring a single vector')
        parser.add_argument('--grid-values', type=str, default='0,1,2,4,8',
                             help='Comma-separated weight values tried per dimension during grid search')
        parser.add_argument('--weak-factor', type=float, default=0.5,
                             help="artist_history_weak/tag_similarity_weak = their strong weight * this "
                                  "factor, not independently swept (default 0.5)")
        parser.add_argument('--min-score', type=float, default=0.0,
                             help='Minimum combined score for a candidate to be accepted at all (default 0)')
        parser.add_argument('--top', type=int, default=10,
                             help='How many best weight combinations to print in --grid-search mode')

    def handle(self, *args, **options):
        limit = options['limit']
        seed = options['seed']
        model_choice = options['model']
        external = options['external']
        min_score = options['min_score']
        tagger_backend = 'timm' if model_choice == 'canary' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger_module, 'HAVE_TIMM', False):
            self.stderr.write(self.style.ERROR(
                "--model canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            ))
            return

        items = get_evaluation_items(limit, seed)
        if not items:
            self.stderr.write(self.style.ERROR(
                'No evaluable items found — need titles+characters+situation all set, plus an image.'
            ))
            return

        # Phase 1 (expensive, runs ONCE total): collect every source's raw
        # candidates per blanked item. No weights involved yet.
        cached = []
        for i, item in enumerate(items):
            imgs = list(item.preview_images.order_by('order'))
            if not imgs and not item.preview_data:
                continue  # no image to run the tagger on — can't evaluate this one

            blank = copy.copy(item)  # in-memory clone, never .save()'d — real DB row untouched
            blank.titles = []
            blank.characters = []
            blank.tags = None
            blank.situation = ''

            collected = views._collect_candidates(blank, external=external, tagger_backend=tagger_backend)
            cached.append((item, collected))
            self.stdout.write(f'Collected item {item.id} ({i + 1}/{len(items)})')

        if not cached:
            self.stderr.write(self.style.ERROR('No items had an image to collect candidates from.'))
            return

        self.stdout.write(f'\n{len(cached)} items ready (model={model_choice}, external={external}, seed={seed})\n')

        def expand(partial_weights):
            w = dict(views.DEFAULT_ENSEMBLE_WEIGHTS)
            w.update(partial_weights)
            w['artist_history_weak'] = w['artist_history'] * options['weak_factor']
            w['tag_similarity_weak'] = w['tag_similarity'] * options['weak_factor']
            w['tagger_group'] = w['tagger']
            return w

        def score_weights(weights):
            title_hits = char_hits = situation_hits = 0
            for item, collected in cached:
                title_vals, _ = views._combine_candidates(collected['title'], weights, min_score, top_k=1)
                char_vals, _ = views._combine_candidates(collected['character'], weights, min_score, top_k=1)
                situ_vals, _ = views._combine_candidates(collected['situation'], weights, min_score, top_k=1)
                if title_vals and title_vals[0] in (item.titles or []):
                    title_hits += 1
                if char_vals and char_vals[0] in (item.characters or []):
                    char_hits += 1
                if situ_vals and situ_vals[0] == item.situation:
                    situation_hits += 1
            n = len(cached)
            return title_hits / n, char_hits / n, situation_hits / n

        # Phase 2 (cheap): score one vector, or sweep a grid.
        if options['weights']:
            weights = expand(json.loads(options['weights']))
            t, c, s = score_weights(weights)
            self.stdout.write(f'weights={weights}')
            self.stdout.write(self.style.SUCCESS(
                f'title={t:.1%}  character={c:.1%}  situation={s:.1%}  avg={(t + c + s) / 3:.1%}'
            ))
            return

        if options['grid_search']:
            grid_values = [float(x) for x in options['grid_values'].split(',')]
            dims = ['hashtag', 'artist_history', 'tag_similarity', 'tagger', 'danbooru', 'classifier']
            combos = list(itertools.product(grid_values, repeat=len(dims)))
            self.stdout.write(f'Grid-searching {len(combos)} weight combinations over {dims}...\n')

            results = []
            for combo in combos:
                weights = expand(dict(zip(dims, combo)))
                t, c, s = score_weights(weights)
                results.append((dict(zip(dims, combo)), t, c, s, (t + c + s) / 3))
            results.sort(key=lambda r: -r[-1])

            self.stdout.write(self.style.SUCCESS(f"\nTop {options['top']} weight combinations by average accuracy:"))
            for partial, t, c, s, avg in results[:options['top']]:
                self.stdout.write(
                    f'  avg={avg:.1%}  title={t:.1%} char={c:.1%} situation={s:.1%}  weights={partial}'
                )
            return

        weights = expand({})
        t, c, s = score_weights(weights)
        self.stdout.write(f'Default weights: {weights}')
        self.stdout.write(self.style.SUCCESS(
            f'title={t:.1%}  character={c:.1%}  situation={s:.1%}  avg={(t + c + s) / 3:.1%}'
        ))
