"""Shared ground-truth item selection for the evaluate_* management commands
(evaluate_tag_count, evaluate_full_pipeline, evaluate_threshold). Not itself a
command — the leading underscore keeps Django's command auto-discovery
(which skips filenames starting with '_') from picking it up, so it's safely
importable as a plain module.
"""
import random

from item.models import Item


def get_evaluation_items(limit, seed=None):
    """Ground truth pool: items with titles+characters+situation all
    confirmed (so there's a real answer to check predictions against) and at
    least one preview image (so the tagger has something to run on).

    `order_by('?')` re-randomizes on every call, so running two evaluate_*
    commands back to back (e.g. comparing --model default vs --model canary)
    would otherwise silently evaluate two DIFFERENT random subsets — any gap
    between their match rates could then be sample noise (which characters/
    titles happened to get picked), not a real model difference. `seed`
    fixes that: candidate pks are sorted deterministically, then Python's
    own random.Random(seed) — independent of the DB's own RNG — picks which
    `limit` of them to use, so the same seed always yields the same sample
    regardless of which model/threshold/tag-count is being varied. Leave
    `seed` unset for the old random-each-run behavior.
    """
    pks = list(
        Item.objects
        .exclude(titles=[]).exclude(titles__isnull=True)
        .exclude(characters=[]).exclude(characters__isnull=True)
        .exclude(situation='').exclude(situation__isnull=True)
        .order_by('pk')
        .values_list('pk', flat=True)
    )
    rng = random.Random(seed) if seed is not None else random.Random()
    sample_pks = rng.sample(pks, k=min(limit, len(pks)))
    return list(Item.objects.filter(pk__in=sample_pks))
