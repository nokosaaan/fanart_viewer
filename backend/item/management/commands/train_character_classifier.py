"""Trains a supplementary character classifier on this app's OWN labeled
images, for characters the public Danbooru-trained taggers structurally
cannot know about (OCs, characters from very recent/niche titles not yet in
Danbooru's tag vocabulary).

Approach: reuse the existing tagger as a frozen feature extractor (no
retraining of the tagger itself — that would need vastly more data/compute
than a personal archive has) and fit a lightweight classifier on top
(scikit-learn LogisticRegression by default). --feature-source picks what
the classifier sees: the tagger's general-tag probability vector ('tags'),
the canary model's pooled embedding ('embedding' — keeps visual information
the tag vocabulary throws away), or both. The saved artifact records which,
and tagger.predict_character reads it, so inference needs no flag.

WHAT IS TRAINED ON
- Solo images: items with exactly one confirmed character.
- With --include-multi-character, also manually region-labeled crops
  (Item.character_regions, drawn in RegionAnnotator.jsx): a human-drawn box
  around ONE person in a group image, cut out and treated as its own image.
  Never automatic person detection alone (an earlier self-training attempt
  was removed as unsound — a coincidental box-count match is not evidence).
- Solo images and region crops are COUNTED TOGETHER against --min-images: a
  newly added character with 3 solo images but 53 labeled regions has 56
  training images and is included. Crops count for less in the fit
  (--region-weight, default 0.5): a hand-drawn box can clip a neighbour or
  miss hair ornaments, so it is noisier evidence than a solo image.
- --max-images-per-character caps ONE character's rows (solo first, regions
  fill the rest) so a long-established character with hundreds of images
  doesn't cost hours of extraction for no accuracy gain; --max-images-per-
  item stops one many-page post from standing in for a whole class.
- --grayscale-augment adds each image's black-and-white version as an extra
  row (same label), so monochrome drawings land near the character instead
  of in a generic "monochrome" cluster.
- A character labelled with a deliberate trait bucket (e.g. "white" for many
  different unnamed OCs with white hair) is an intentional class and should
  NOT be --exclude'd; --exclude is for accidental mislabels such as "the
  whole cast of title X", which only teaches "several people present".

HOW THE ACCURACY IS REPORTED
The holdout is split by ITEM, not by row: pages/crops of one post never sit
on both sides (otherwise the model scores by recognising the post, which
inflated an earlier random-split figure by several points). A class whose
images all come from ONE post can't be assessed (holding it out leaves no
training rows) and is trained on but left out of the holdout. The saved
model is refit on every row after the report (--no-refit-all to keep the
split model); the printed accuracy describes the split model. Holdout
accuracy is also printed separately for solo images and region crops.

THE EXPENSIVE PART is feature extraction (one forward pass per image, ~8s
on CPU). Features are cached by each image's stable key (a PreviewImage id,
or item:<id> / <item>:<image index>:<box> for crops), so --update-cache only
extracts what is new, and checkpoints during a long run. --use-cache refits
straight from a cache without touching the DB (never goes stale-checked, so
it is only right when nothing was added since).

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --backend canary --feature-source embedding --include-multi-character --min-images 5

  # Ongoing use as the DB grows: only newly-added images get extracted
  python manage.py train_character_classifier --backend canary --feature-source embedding \\
      --include-multi-character --update-cache /app/data/tagger/character_features_canary_embedding.joblib

  # Tweak and refit WITHOUT extracting or touching the DB
  python manage.py train_character_classifier --backend canary --feature-source embedding \\
      --use-cache /app/data/tagger/character_features_canary_embedding.joblib --exclude 牢屋敷メンバー
"""
import importlib.util
import os
import time
import zlib
from collections import defaultdict

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from item.models import Item, CharacterAliasGroup, PreviewImage
from item import tagger


def _have_torch():
    # Same lazy find_spec check tagger.py uses for HAVE_TIMM — avoids
    # importing torch (and paying its import cost) just to check whether
    # --classifier metric_learning is even usable.
    return importlib.util.find_spec('torch') is not None


class NearestCentroidClassifier:
    """A simple metric-learning-style classifier (the same idea as
    Prototypical Networks: https://arxiv.org/abs/1703.05175, reported at
    ~89% on 5-way-5-shot ANIME character classification in a recent
    survey) — each class is represented by the mean ("prototype") of its
    own training features, and a query is classified by softmax over
    cosine similarity to every prototype. No gradient training happens
    here — the representation learning already happened in whichever
    upstream feature extractor produced these vectors (tagger.py's ONNX/
    canary backend); this only computes class means and a similarity-based
    pseudo-probability. Exposes the same fit/predict/predict_proba/
    classes_/score interface as an sklearn estimator, so it's a drop-in
    swap anywhere a trained classifier is used (this command,
    tagger.predict_character).
    """

    def __init__(self, temperature=10.0):
        # Scales cosine similarity before softmax — higher = more peaked
        # (confident) probabilities. 10.0 is a common starting point for
        # cosine-similarity-based softmax losses (e.g. ArcFace-style
        # setups typically use a comparable scale).
        self.temperature = temperature

    def fit(self, X, y):
        X, y = np.asarray(X), np.asarray(y)
        self.classes_ = np.unique(y)
        prototypes = np.stack([X[y == c].mean(axis=0) for c in self.classes_])
        norms = np.linalg.norm(prototypes, axis=1, keepdims=True)
        self._proto_unit = prototypes / np.clip(norms, 1e-8, None)
        return self

    def _cosine_sim(self, X):
        X = np.atleast_2d(np.asarray(X))
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        X_unit = X / np.clip(norms, 1e-8, None)
        return X_unit @ self._proto_unit.T

    def predict_proba(self, X):
        sims = self._cosine_sim(X) * self.temperature
        sims = sims - sims.max(axis=1, keepdims=True)  # numerical stability
        exp = np.exp(sims)
        return exp / exp.sum(axis=1, keepdims=True)

    def predict(self, X):
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]

    def score(self, X, y):
        return float(np.mean(self.predict(X) == np.asarray(y)))


def _build_arcface_backbone(in_dim, hidden_dim, embedding_dim):
    # Module-level (not nested in a method) so the resulting nn.Module is
    # picklable by joblib — a class defined inside a function/method has no
    # importable dotted path, which makes `pickle`/`joblib.dump` fail with
    # "Can't pickle <locals>._Backbone" as soon as the trained classifier
    # (which holds one of these as self._backbone) is saved.
    import torch.nn as nn

    return nn.Sequential(
        nn.Linear(in_dim, hidden_dim),
        nn.BatchNorm1d(hidden_dim),
        nn.ReLU(inplace=True),
        nn.Dropout(0.3),
        nn.Linear(hidden_dim, embedding_dim),
    )


class MetricLearningClassifier:
    """Real (gradient-trained) metric learning: an ArcFace-style additive
    angular margin loss (https://arxiv.org/abs/1801.07698) on top of a
    small learned embedding projection. This is the "priority 1" approach
    from the architecture-comparison literature survey (ArcFace /
    Prototypical Networks; Prototypical Networks reported ~89% on 5-way
    5-shot ANIME character classification) — unlike
    NearestCentroidClassifier (which just takes an unsupervised mean of the
    frozen upstream features, no training at all), this actually learns a
    projection so that same-character features are pulled closer together
    and different-character features pushed apart, directly optimizing for
    the cosine-similarity metric the classifier is evaluated with.

    Architecture: Linear -> BatchNorm -> ReLU -> Dropout -> Linear down to
    `embedding_dim`, L2-normalized. Each class also gets a learned
    (L2-normalized) weight vector in that same embedding space — these
    behave like Prototypical Networks' prototypes, except learned via
    backprop instead of computed as an empirical mean. During training,
    the true class's cosine similarity gets an additive angular margin
    before the softmax (ArcFace's core trick — it directly enlarges the
    decision margin between classes in angle space, not just in raw
    logit-value space like ordinary softmax does). At inference, no margin
    is applied — predict_proba is a plain temperature-scaled softmax over
    cosine similarity to every class's weight vector.

    Requires torch (see `_have_torch()` / tagger.HAVE_TIMM's build arg) —
    imported lazily so selecting any other --classifier choice never pays
    torch's import cost. Works on top of either --feature-source (onnx tag
    probabilities or canary embeddings); torch itself doesn't care.
    """

    def __init__(self, embedding_dim=256, hidden_dim=512, margin=0.5, scale=30.0,
                 epochs=60, batch_size=64, lr=1e-3, weight_decay=1e-4, random_state=42):
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.margin = margin
        self.scale = scale
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.weight_decay = weight_decay
        self.random_state = random_state

    def fit(self, X, y):
        import torch
        import torch.nn as nn
        import torch.nn.functional as F

        torch.manual_seed(self.random_state)
        X = np.asarray(X, dtype=np.float32)
        self.classes_ = np.unique(y)
        class_index = {c: i for i, c in enumerate(self.classes_)}
        y_idx = np.array([class_index[c] for c in y], dtype=np.int64)
        n_classes = len(self.classes_)
        in_dim = X.shape[1]

        # Same sklearn 'balanced' formula the default LogisticRegression
        # head already uses (see make_classifier() below) — unlike that
        # one, this custom training loop had no per-class weighting at all,
        # so a character with far more single-character images than
        # others got proportionally more gradient updates per epoch (both
        # from appearing in more batches AND from cross_entropy weighting
        # every sample equally), pulling the learned embedding space/
        # prototypes toward separating it well at the expense of
        # sparser classes. Weighting the loss (not just resampling
        # batches) fixes this without throwing away any real data.
        class_counts = np.bincount(y_idx, minlength=n_classes).astype(np.float64)
        class_weights_np = len(y_idx) / (n_classes * class_counts)
        class_weights = torch.from_numpy(class_weights_np.astype(np.float32))

        backbone = _build_arcface_backbone(in_dim, self.hidden_dim, self.embedding_dim)
        # ArcFace class weight vectors — one per class, in the same
        # embedding space, playing the role of a learned prototype.
        class_weight = nn.Parameter(torch.randn(n_classes, self.embedding_dim) * 0.01)

        optimizer = torch.optim.Adam(
            list(backbone.parameters()) + [class_weight],
            lr=self.lr, weight_decay=self.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs)

        X_t = torch.from_numpy(X)
        y_t = torch.from_numpy(y_idx)
        n = len(X_t)
        cos_m, sin_m = float(np.cos(self.margin)), float(np.sin(self.margin))
        # Below this cosine, true_angle + margin would exceed pi (where
        # cos stops being monotonically decreasing) — ArcFace's standard
        # fallback keeps the loss well-behaved for those examples instead
        # of letting the margin term flip sign.
        threshold = float(np.cos(np.pi - self.margin))

        rng = np.random.RandomState(self.random_state)
        backbone.train()
        for _epoch in range(self.epochs):
            perm = rng.permutation(n)
            for start in range(0, n, self.batch_size):
                idx = perm[start:start + self.batch_size]
                if len(idx) < 2:
                    continue  # BatchNorm needs >= 2 rows in train mode
                xb, yb = X_t[idx], y_t[idx]

                emb = F.normalize(backbone(xb), dim=1)
                w = F.normalize(class_weight, dim=1)
                cosine = emb @ w.t()

                sine = torch.sqrt((1.0 - cosine.clamp(-1 + 1e-7, 1 - 1e-7) ** 2))
                phi = cosine * cos_m - sine * sin_m
                phi = torch.where(cosine > threshold, phi, cosine - self.margin * sin_m)

                one_hot = torch.zeros_like(cosine)
                one_hot.scatter_(1, yb.view(-1, 1), 1.0)
                logits = (one_hot * phi + (1.0 - one_hot) * cosine) * self.scale

                loss = F.cross_entropy(logits, yb, weight=class_weights)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            scheduler.step()

        backbone.eval()
        self._backbone = backbone
        self._class_weight = F.normalize(class_weight.detach(), dim=1)
        return self

    def _embed(self, X):
        import torch
        import torch.nn.functional as F
        with torch.no_grad():
            self._backbone.eval()
            raw = self._backbone(torch.from_numpy(np.asarray(X, dtype=np.float32)))
            return F.normalize(raw, dim=1)

    def predict_proba(self, X):
        import torch.nn.functional as F
        emb = self._embed(X)
        logits = (emb @ self._class_weight.t()) * self.scale
        return F.softmax(logits, dim=1).detach().numpy()

    def predict(self, X):
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]

    def score(self, X, y):
        return float(np.mean(self.predict(X) == np.asarray(y)))


_SOLO, _REGION = 'solo', 'region'
_GRAY_SUFFIX = '|gray'

# Default cap on how many rows of ONE character the classifier trains on
# (solo images preferred, manually region-labeled crops fill the remainder).
# Beyond this a character gains almost nothing (see the learning-curve notes
# in the --max-images-per-character help) while every extra row costs a ~8s
# forward pass and fit time.
DEFAULT_MAX_PER_CHARACTER = 150
# A region crop is a human-drawn box around ONE person cut out of a group
# image: it can clip a neighbour, include part of another character, or
# miss hair ornaments -- noisier evidence of identity than a solo image,
# so it counts for less in the fit (see --region-weight).
DEFAULT_MAX_PER_ITEM = 10  # no accuracy change vs. uncapped at 10 or 5 in item-grouped CV; fewer rows to extract
DEFAULT_REGION_WEIGHT = 0.5
DEFAULT_GRAY_WEIGHT = 0.5


def _item_id_of_key(key):
    """Item id encoded in a region key ('<item>:<image_index>:<box>') or an
    image key ('pi:<previewimage id>' has none -> None)."""
    head = key.split(':', 1)[0]
    return int(head) if head.isdigit() else None


class Command(BaseCommand):
    help = (
        'Trains a character classifier on this app\'s own labeled images (single-character items and, '
        'with --include-multi-character, manually region-labeled crops), using the existing tagger as a '
        'frozen feature extractor.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--min-images', type=int, default=15,
                             help='Minimum TOTAL training images a character needs to be included — solo '
                                  'images plus (with --include-multi-character) manually region-labeled '
                                  'crops, counted together (default 15 — see character_image_stats)')
        parser.add_argument('--max-images-per-character', type=int, default=DEFAULT_MAX_PER_CHARACTER,
                             help='Cap on how many images of ONE character are used for training, solo images '
                                  'first and region crops filling the rest (0 = no cap; default '
                                  f'{DEFAULT_MAX_PER_CHARACTER}). Characters above the cap stop gaining accuracy '
                                  'long before they stop costing extraction time (~8s per image on CPU), so the '
                                  'surplus is neither extracted nor trained on. Already-cached images are '
                                  'preferred when choosing which ones to keep.')
        parser.add_argument('--max-images-per-item', type=int, default=DEFAULT_MAX_PER_ITEM,
                             help='Cap on how many images of ONE character come from ONE post (0 = no cap; '
                                  f'default {DEFAULT_MAX_PER_ITEM}). A 25-page post is one piece of evidence about a '
                                  'character, not 25, and left alone it lets that single post dominate the class.')
        parser.add_argument('--region-weight', type=float, default=DEFAULT_REGION_WEIGHT,
                             help='Training weight of a manually region-labeled crop relative to a solo image '
                                  f'(default {DEFAULT_REGION_WEIGHT}) — a hand-drawn box around one person in a '
                                  'group image is less reliable evidence of identity than a solo image.')
        parser.add_argument('--grayscale-augment', action='store_true',
                             help='Also extract each training image\'s grayscale version and train on it as an '
                                  'extra row (same label, --gray-weight), so a black-and-white drawing of a '
                                  'character lands near that character instead of in a "monochrome" cluster. '
                                  'Doubles extraction cost. The holdout is always real images only.')
        parser.add_argument('--gray-weight', type=float, default=DEFAULT_GRAY_WEIGHT,
                             help=f'Training weight of a grayscale-augmented row (default {DEFAULT_GRAY_WEIGHT}).')
        parser.add_argument('--no-refit-all', action='store_true',
                             help='Save the model fit on the training split only (the one the holdout accuracy '
                                  'describes) instead of refitting on every row afterwards.')
        parser.add_argument('--exclude', type=str, default='',
                             help='Comma-separated character names to drop entirely, even if they clear '
                                  '--min-images — for labels that are not one visually-coherent character '
                                  '(a catch-all OC bucket, a "whole cast" group tag, etc.)')
        parser.add_argument('--feature-source', type=str, default='tags',
                             help="What the classifier sees, one or several joined with '+': 'tags' (the "
                                  "tagger's general-tag probability vector), 'embedding' (the canary/timm "
                                  "model's pooled pre-classification-head embedding — keeps visual information "
                                  "the tag vocabulary discards; needs --backend canary), 'ccip' (deepghs' "
                                  "same-character embedding via imgutils — trained so pictures of one character "
                                  "land together whatever the outfit; needs no tagger forward pass at all). "
                                  "E.g. 'tags+ccip'. 'both' = 'tags+embedding'. The artifact records the choice, "
                                  "so inference needs no flag.")
        parser.add_argument('--backend', choices=['onnx', 'canary'], default='canary',
                             help="Tagger backend to use as the frozen feature extractor: 'onnx' (small, "
                                  "always available) or 'canary' (needs INSTALL_TIMM_TAGGER=1). Whichever "
                                  "is chosen, the SAME backend must be used later at inference time to "
                                  "reproduce these exact features — the saved artifact records which one.")
        parser.add_argument('--test-size', type=float, default=0.15,
                             help='Fraction of items held out to report accuracy (default 0.15). The split is '
                                  'by ITEM, not by row: several crops/pages of one post never straddle '
                                  'train and test.')
        parser.add_argument('--random-state', type=int, default=42,
                             help='Random seed for the train/holdout split (default 42)')
        parser.add_argument('--output', type=str, default=None,
                             help='Where to save the trained classifier (.joblib). Default: '
                                  "character_classifier_<backend>.joblib under the tagger's own cache dir.")
        parser.add_argument('--feature-cache', type=str, default=None,
                             help='Where to save extracted features after extraction, for reuse by a later '
                                  "--use-cache run. Default: character_features_<backend>[_<feature source>]"
                                  ".joblib under the tagger's own cache dir.")
        parser.add_argument('--use-cache', type=str, default=None,
                             help='Path to a previously-saved --feature-cache/--update-cache file — skip DB '
                                  'scanning and feature extraction entirely and refit straight from these '
                                  'cached features, exactly as saved (still applies --exclude/--min-images/'
                                  '--max-images-per-character as filters first).')
        parser.add_argument('--update-cache', type=str, default=None,
                             help='Path to a --feature-cache/--update-cache file to incrementally refresh: '
                                  'scans the DB as usual, but reuses each already-cached image\'s feature '
                                  'vector instead of re-running the (expensive) tagger forward pass on it, '
                                  "only extracting images that are new (or weren't yet cached). The file is "
                                  'overwritten (or --feature-cache written instead, if given) with the refreshed '
                                  'set, and checkpointed during a long extraction so an interrupted run keeps '
                                  'what it already computed. Mutually exclusive with --use-cache.')
        parser.add_argument('--include-multi-character', action='store_true',
                             help='Also learn from multi-character items, but ONLY the ones a human has '
                                  'manually region-labeled (Item.character_regions — see module docstring). '
                                  'Their crops count toward --min-images together with solo images.')
        parser.add_argument('--classifier',
                             choices=['logreg', 'mlp', 'nearest_centroid', 'metric_learning'], default='logreg',
                             help="Classifier head to fit on top of the (frozen) features: 'logreg' "
                                  "(current default, linear), 'mlp' (a small non-linear network, tests "
                                  "whether classifier capacity is the bottleneck), 'nearest_centroid' "
                                  "(a simple metric-learning baseline — classify by cosine similarity to "
                                  "each class's mean feature vector, no gradient training), or "
                                  "'metric_learning' (real gradient-trained metric learning: an ArcFace-style "
                                  "learned embedding projection + margin loss — the literature survey's "
                                  "priority-1 approach; requires torch, see requirements-timm.txt). Only "
                                  "'logreg' honors --region-weight/--gray-weight.")

    # ------------------------------------------------------------------
    # Candidate survey: what COULD be trained on, before any extraction.
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_region_label(names, linked_groups):
        """Single training label for a region's character names, or None
        when ambiguous. A region can carry 2+ names either because
        person-detection merged two overlapping people into one box
        (genuinely ambiguous — no single identity to teach) or because the
        same person legitimately has 2+ names (a magical girl's real name
        + transformed name). A CharacterAliasGroup with linked=True whose
        name-set matches exactly marks the second case: it's trained on
        under that group's alphabetically-first name (views.
        _expand_character_alias re-expands it at inference)."""
        if len(names) == 1:
            return names[0]
        group = linked_groups.get(tuple(sorted(set(names))))
        return group[0] if group is not None else None

    def _survey(self, include_regions):
        """[{'kind','char','item_id','key', ...}, ...] for every solo image
        and (include_regions) region crop in the DB, WITHOUT reading any
        image bytes — so a character's TOTAL support (solo + regions) is
        known before deciding what's worth extracting."""
        cands = []
        annotated_ids = set()
        n_invalid = n_ambiguous = 0

        if include_regions:
            linked_groups = {
                tuple(sorted(set(g.characters))): sorted(set(g.characters))
                for g in CharacterAliasGroup.objects.filter(linked=True)
            }
            for item in Item.objects.exclude(character_regions=[]).only('id', 'character_regions'):
                annotated_ids.add(item.id)
                for region in (item.character_regions or []):
                    box, names = region.get('box'), region.get('characters') or []
                    if not box or not names:
                        continue
                    try:
                        x1, y1, x2, y2 = (float(v) for v in box)
                    except (TypeError, ValueError):
                        n_invalid += 1
                        continue
                    if x2 <= x1 or y2 <= y1:
                        n_invalid += 1  # e.g. "Coordinate 'right' is less than 'left'" at extraction time
                        continue
                    label = self._resolve_region_label(names, linked_groups)
                    if label is None:
                        n_ambiguous += 1
                        continue
                    image_index = region.get('image_index')
                    cands.append({
                        'kind': _REGION, 'char': label, 'item_id': item.id, 'image_index': image_index,
                        'box': tuple(box), 'key': f'{item.id}:{image_index}:{",".join(str(v) for v in box)}',
                    })

        preview_ids = defaultdict(list)
        for item_id, pid in PreviewImage.objects.order_by('item_id', 'order').values_list('item_id', 'id'):
            preview_ids[item_id].append(pid)
        legacy_preview = set(Item.objects.filter(preview_data__isnull=False).values_list('id', flat=True))
        for item_id, characters in Item.objects.exclude(characters=[]).exclude(characters__isnull=True).values_list(
                'id', 'characters'):
            chars = [c for c in (characters or []) if c]
            if len(chars) != 1:
                continue  # solo = single-character items only
            if item_id in annotated_ids:
                continue  # its hand-drawn crop(s) supersede the whole frame
            char = chars[0]
            if preview_ids.get(item_id):
                for pid in preview_ids[item_id]:
                    cands.append({'kind': _SOLO, 'char': char, 'item_id': item_id, 'key': f'pi:{pid}'})
            elif item_id in legacy_preview:
                cands.append({'kind': _SOLO, 'char': char, 'item_id': item_id, 'key': f'item:{item_id}'})

        if n_invalid:
            self.stdout.write(self.style.WARNING(
                f'{n_invalid} region(s) skipped: invalid box (right<=left or bottom<=top, or not numbers) — '
                'see normalize_character_regions.'
            ))
        if n_ambiguous:
            self.stdout.write(self.style.WARNING(
                f'{n_ambiguous} region(s) skipped: 2+ characters on one box with no linked CharacterAliasGroup '
                '(ambiguous identity).'
            ))
        return cands

    @staticmethod
    def _cands_from_cache(base_cache):
        """The --use-cache counterpart of _survey: candidates (with their
        features already attached) straight from the cache file."""
        cands, region_item_ids = [], set()
        for char, feat, key in (base_cache.get('manual_rows') or []):
            if key is None or key.endswith(_GRAY_SUFFIX):
                continue
            iid = _item_id_of_key(key)
            region_item_ids.add(iid)
            cands.append({'kind': _REGION, 'char': char, 'item_id': iid, 'key': key, 'feat': feat})
        for item_id, char, feat, key in base_cache['rows']:
            if key is None or key.endswith(_GRAY_SUFFIX) or item_id in region_item_ids:
                continue
            cands.append({'kind': _SOLO, 'char': char, 'item_id': item_id, 'key': key, 'feat': feat})
        return cands

    @staticmethod
    def _select(cands, exclude, min_images, cap, cached_keys, per_item_cap=0):
        """-> (selected, per_char) where per_char is
        {char: (n_solo, n_region, n_used)} for every eligible character.

        Eligibility is decided on the TOTAL (solo + region) count, so a
        newly added character with 3 solo images but 53 labeled regions
        has 56 and is included. Above `cap`, solo images are kept first
        (more reliable), then regions; within each tier already-cached
        rows are preferred (free), then a stable hash order (the same
        subset every run, not a different random draw each time)."""
        def priority(c):
            return (c['kind'] != _SOLO, c['key'] not in cached_keys, zlib.crc32(c['key'].encode('utf-8')))

        by_char = defaultdict(list)
        for c in cands:
            if c['char'] not in exclude:
                by_char[c['char']].append(c)
        if per_item_cap:
            # 25 pages of ONE post are one piece of evidence about a character,
            # not 25 -- left alone they let a single post dominate its class.
            for char, cs in by_char.items():
                per_item = defaultdict(list)
                for c in sorted(cs, key=priority):
                    per_item[c['item_id']].append(c)
                by_char[char] = [c for group in per_item.values() for c in group[:per_item_cap]]
        cap = max(cap, min_images) if cap else 0
        selected, per_char = [], {}
        for char, cs in by_char.items():
            if len(cs) < min_images:
                continue
            n_solo = sum(1 for c in cs if c['kind'] == _SOLO)
            if cap and len(cs) > cap:
                cs = sorted(cs, key=priority)[:cap]
            per_char[char] = (n_solo, sum(1 for c in by_char[char] if c['kind'] == _REGION), len(cs))
            selected.extend(cs)
        return selected, per_char

    # ------------------------------------------------------------------

    def _compute_feature(self, image_bytes, tagger_backend, feature_source, gray=False):
        """(feature_vector, feature_names) for one image: the parts named in
        `feature_source` ('tags', 'embedding', 'ccip'), concatenated in that
        order. Tags and embedding come out of ONE tagger forward pass; CCIP is
        its own (much cheaper) model. Names are placeholders for the
        embedding/CCIP dimensions — only len() matters to the feature-length
        guard and the cache's ordering check."""
        if gray:
            image_bytes = tagger.to_grayscale_bytes(image_bytes)
        parts = tagger.feature_parts(feature_source)
        raw = embedding = None
        if 'tags' in parts or 'embedding' in parts:
            raw, embedding = tagger._raw_predict_with_embedding(image_bytes, None, tagger_backend)
        vectors, names = [], []
        for part in parts:
            if part == 'tags':
                preds, tag_names, _r, general_idx, _c = raw
                vectors.append(np.asarray(preds, dtype=np.float32)[general_idx])
                names += [tag_names[i] for i in general_idx]
            elif part == 'embedding':
                if embedding is None:
                    raise RuntimeError("feature source 'embedding' needs the canary/timm backend")
                vectors.append(embedding)
                names += [f'embedding_{i}' for i in range(len(embedding))]
            else:
                vec = tagger.ccip_feature(image_bytes)
                vectors.append(vec)
                names += [f'ccip_{i}' for i in range(len(vec))]
        return (np.concatenate(vectors) if len(vectors) > 1 else vectors[0]), names

    def _image_bytes_for(self, cand, last):
        """Raw image bytes for a candidate (crop applied for a region);
        `last` is a one-entry cache of the previous (item, image) so several
        boxes on one image decode it once. None if unavailable."""
        from item.views import _select_image_bytes

        key = cand['key']
        if cand['kind'] == _SOLO:
            if key.startswith('pi:'):
                return PreviewImage.objects.filter(id=int(key[3:])).values_list('data', flat=True).first()
            return Item.objects.filter(id=cand['item_id']).values_list('preview_data', flat=True).first()
        ident = (cand['item_id'], cand['image_index'])
        if last.get('ident') != ident:
            item = Item.objects.only('id').get(id=cand['item_id'])
            image_bytes, _idx = _select_image_bytes(item, cand['image_index'])
            last['ident'], last['bytes'] = ident, image_bytes
        if last['bytes'] is None:
            return None
        return tagger._crop_with_padding(last['bytes'], cand['box'])

    def _extract(self, selected, cached, tagger_backend, feature_source, gray, expected_names, checkpoint):
        """Attach 'feat' (and 'gray_feat' when `gray`) to every selected
        candidate, reusing `cached` {key: feature} where possible. Returns
        (rows_ok, general_tag_names, n_reused, n_new). `checkpoint(rows)` is
        called every few hundred new extractions so a multi-hour run that is
        interrupted keeps what it computed."""
        general_tag_names = expected_names
        n_base_cached = sum(1 for c in selected if c['key'] in cached)
        todo = [c for c in selected if c['key'] not in cached or (gray and c['key'] + _GRAY_SUFFIX not in cached)]
        todo.sort(key=lambda c: (c['kind'], c['item_id'], str(c.get('image_index'))))
        self.stdout.write(
            f'\nExtracting features for {len(selected)} images (backend={tagger_backend}, '
            f'feature_source={feature_source}{", +grayscale" if gray else ""}) — '
            f'{n_base_cached} already cached, {len(selected) - n_base_cached} new'
            + (f' ({len(todo)} image(s) need extraction, the rest only their grayscale version)' if gray else '')
            + '...'
        )
        n_new, t0, last, done = 0, time.time(), {}, 0
        for cand in todo:
            done += 1
            try:
                image_bytes = self._image_bytes_for(cand, last)
                if image_bytes is None:
                    self.stderr.write(f'item {cand["item_id"]}: no image available for {cand["key"]}, skipping')
                    continue
                image_bytes = bytes(image_bytes)
                if cand['key'] not in cached:
                    feature, names = self._compute_feature(image_bytes, tagger_backend, feature_source)
                    if general_tag_names is None:
                        general_tag_names = names
                    elif names != general_tag_names:
                        self.stderr.write(f'item {cand["item_id"]}: feature ordering mismatch, skipping')
                        continue
                    cached[cand['key']] = feature
                if gray and cand['key'] + _GRAY_SUFFIX not in cached:
                    gfeature, _n = self._compute_feature(image_bytes, tagger_backend, feature_source, gray=True)
                    cached[cand['key'] + _GRAY_SUFFIX] = gfeature
                n_new += 1
            except Exception as e:
                self.stderr.write(f'item {cand["item_id"]}: feature extraction failed ({e}), skipping {cand["key"]}')
                continue
            if done % 50 == 0 or done == len(todo):
                self.stdout.write(f'  {done}/{len(todo)} newly extracted ({time.time() - t0:.0f}s elapsed)')
            if n_new and n_new % 300 == 0:
                checkpoint()
        rows = []
        for c in selected:
            if c['key'] in cached:
                c['feat'] = cached[c['key']]
                c['gray_feat'] = cached.get(c['key'] + _GRAY_SUFFIX)
                rows.append(c)
        return rows, general_tag_names, n_base_cached, n_new

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        try:
            import joblib
            from sklearn.linear_model import LogisticRegression
            from sklearn.neural_network import MLPClassifier
            from sklearn.model_selection import StratifiedGroupKFold, train_test_split
            from sklearn.metrics import classification_report
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError as e:
            # raise, not self.stderr.write(...); return -- this command runs
            # as a subprocess the GUI panel (see item/classifier_training.py)
            # only learns the outcome of via its exit code; a bare `return`
            # exits 0 (success) even though nothing was trained.
            raise CommandError(f'scikit-learn/joblib not available: {e}')

        classifier_choice = options['classifier']
        min_images = options['min_images']
        cap = options['max_images_per_character']
        region_weight, gray_weight = options['region_weight'], options['gray_weight']
        exclude = {c.strip() for c in options['exclude'].split(',') if c.strip()}
        backend_choice = options['backend']
        feature_source = options['feature_source']
        gray = options['grayscale_augment']
        include_regions = options['include_multi_character']
        tagger_backend = 'timm' if backend_choice == 'canary' else 'onnx'

        needs_forward = any(p in ('tags', 'embedding') for p in tagger.feature_parts(feature_source))
        if needs_forward and tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            raise CommandError(
                "--backend canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            )
        parts = tagger.feature_parts(feature_source)
        unknown = [p for p in parts if p not in ('tags', 'embedding', 'ccip')]
        if unknown or not parts:
            raise CommandError(f"--feature-source: unknown part(s) {unknown or feature_source!r} (use tags/embedding/ccip joined by '+').")
        if 'embedding' in parts and backend_choice != 'canary':
            raise CommandError(f'--feature-source {feature_source} requires --backend canary.')
        if 'ccip' in parts and not (getattr(tagger, 'HAVE_CCIP', False) and tagger.ccip_available()):
            raise CommandError("--feature-source with 'ccip' needs dghs-imgutils (see requirements.txt).")
        if classifier_choice == 'metric_learning' and not _have_torch():
            raise CommandError(
                "--classifier metric_learning requires the optional 'torch' dependency "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg), which is not installed.'
            )
        use_cache_path, update_cache_path = options['use_cache'], options['update_cache']
        if use_cache_path and update_cache_path:
            raise CommandError(
                '--use-cache と --update-cache は同時に指定できません(用途が異なります — '
                'どちらか一方を選んでください)。'
            )

        def make_classifier():
            if classifier_choice == 'mlp':
                return MLPClassifier(hidden_layer_sizes=(128,), max_iter=500, alpha=1e-2,
                                      random_state=options['random_state'])
            if classifier_choice == 'nearest_centroid':
                return NearestCentroidClassifier()
            if classifier_choice == 'metric_learning':
                return MetricLearningClassifier(random_state=options['random_state'])
            # class balance is applied through sample weights below
            if tagger.feature_parts(feature_source) == ['tags']:
                return LogisticRegression(max_iter=2000)
            # embedding/CCIP features have arbitrary per-dimension scale, so they
            # are standardized; C=0.3 sat on the flat top of the sweep for
            # embedding+CCIP (0.1 and 0.3 tied; 1 and 10 were worse or equal).
            # Untuned when 'tags' (11.6k dims, dominating after scaling) is
            # mixed in -- prefer embedding+ccip.
            return Pipeline([('scale', StandardScaler()), ('clf', LogisticRegression(max_iter=3000, C=0.3))])

        def fit_classifier(X_fit, y_fit, w_fit):
            clf = make_classifier()
            if classifier_choice == 'logreg':
                if hasattr(clf, 'steps'):
                    clf.fit(X_fit, y_fit, **{f'{clf.steps[-1][0]}__sample_weight': w_fit})
                else:
                    clf.fit(X_fit, y_fit, sample_weight=w_fit)
            else:
                clf.fit(X_fit, y_fit)
            return clf

        # ---- load cache -------------------------------------------------
        base_cache = None
        if use_cache_path:
            self.stdout.write(f"Loading cached features from {use_cache_path}...")
            base_cache = joblib.load(use_cache_path)
        elif update_cache_path and os.path.exists(update_cache_path):
            self.stdout.write(f"Loading cached features from {update_cache_path} to reuse where possible...")
            base_cache = joblib.load(update_cache_path)
        general_tag_names = None
        if base_cache is not None:
            if base_cache.get('backend') != tagger_backend or base_cache.get('feature_source', 'tags') != feature_source:
                raise CommandError(
                    f"Cache was extracted with backend={base_cache.get('backend')!r}/"
                    f"feature_source={base_cache.get('feature_source', 'tags')!r}, but backend={tagger_backend!r}/"
                    f"feature_source={feature_source!r} was requested — features aren't compatible."
                )
            general_tag_names = base_cache['general_tag_names']
            base_cache['rows'] = [r if len(r) == 4 else (*r, None) for r in base_cache['rows']]

        default_cache_name = (
            f'character_features_{backend_choice}.joblib' if feature_source == 'tags'
            else f'character_features_{backend_choice}_{feature_source.replace("+", "_")}.joblib'
        )
        feature_cache_path = (
            options['feature_cache'] or update_cache_path or os.path.join(tagger._data_dir(), default_cache_name)
        )

        cached = {}
        if base_cache is not None:
            for _iid, _char, feat, key in base_cache['rows']:
                if key is not None:
                    cached[key] = feat
            for _char, feat, key in (base_cache.get('manual_rows') or []):
                if key is not None:
                    cached[key] = feat

        # ---- survey, select, extract --------------------------------------
        if use_cache_path:
            cands = self._cands_from_cache(base_cache)
            self.stdout.write(f'Loaded {len(cands)} cached feature row(s) (no DB scan).\n')
        else:
            cands = self._survey(include_regions)
        selected, per_char = self._select(cands, exclude, min_images, cap, set(cached), options['max_images_per_item'])
        if len(per_char) < 2:
            raise CommandError(
                f'Only {len(per_char)} character(s) have >= {min_images} images (solo'
                f'{" + region" if include_regions else ""}) after --exclude — need at least 2 classes.'
            )
        n_solo_sel = sum(1 for c in selected if c['kind'] == _SOLO)
        self.stdout.write(
            f'{len(per_char)} characters qualify (>= {min_images} images, solo + region counted together; '
            f'cap {cap or "none"}/character): {n_solo_sel} solo + {len(selected) - n_solo_sel} region row(s) selected.'
        )
        for c, (ns, nr, nu) in sorted(per_char.items(), key=lambda kv: -kv[1][2]):
            capped = f'  (capped from {ns + nr})' if nu < ns + nr else ''
            self.stdout.write(f'  {nu:>5}  {c}  [{ns} solo + {nr} region]{capped}')

        def write_cache():
            if use_cache_path:
                return
            solo_rows, manual_rows = [], []
            by_key = {c['key']: c for c in cands}
            for key, feat in cached.items():
                base = key[:-len(_GRAY_SUFFIX)] if key.endswith(_GRAY_SUFFIX) else key
                cand = by_key.get(base)
                if cand is None:
                    continue  # a no-longer-eligible row is dropped, never reused under a stale label
                if cand['kind'] == _SOLO:
                    solo_rows.append((cand['item_id'], cand['char'], feat, key))
                else:
                    manual_rows.append((cand['char'], feat, key))
            joblib.dump({'rows': solo_rows, 'manual_rows': manual_rows, 'backend': tagger_backend,
                         'feature_source': feature_source, 'general_tag_names': general_tag_names}, feature_cache_path)

        if use_cache_path:
            rows = [c for c in selected]
            for c in rows:
                c['gray_feat'] = cached.get(c['key'] + _GRAY_SUFFIX)
        else:
            rows, general_tag_names, n_reused, n_new = self._extract(
                selected, cached, tagger_backend, feature_source, gray, general_tag_names, write_cache,
            )
            self.stdout.write(f'{n_reused} image(s) reused from cache, {n_new} newly extracted.\n')
            write_cache()
            self.stdout.write(self.style.SUCCESS(f'Cached features to {feature_cache_path}\n'))
        if len(rows) < 2:
            raise CommandError('特徴抽出に失敗しました(詳細は上記のログを確認してください)。')

        # ---- assemble matrix ----------------------------------------------
        feats, labels, groups, kinds, aug, base_w = [], [], [], [], [], []
        for c in rows:
            kw = 1.0 if c['kind'] == _SOLO else region_weight
            feats.append(c['feat']); labels.append(c['char']); groups.append(c['item_id'])
            kinds.append(c['kind']); aug.append(False); base_w.append(kw)
            if gray and c.get('gray_feat') is not None:
                feats.append(c['gray_feat']); labels.append(c['char']); groups.append(c['item_id'])
                kinds.append(c['kind']); aug.append(True); base_w.append(kw * gray_weight)
        X = np.stack(feats).astype(np.float32)
        y, groups = np.array(labels), np.array(groups)
        kinds, aug, base_w = np.array(kinds), np.array(aug), np.array(base_w, dtype=np.float64)

        # class-balanced sample weights over the EFFECTIVE (weighted) class mass
        mass = defaultdict(float)
        for lab, w in zip(y, base_w):
            mass[lab] += w
        total_mass, n_classes = sum(mass.values()), len(mass)
        weights = np.array([w * total_mass / (n_classes * mass[lab]) for lab, w in zip(y, base_w)])

        self.stdout.write(f'\n{n_classes} characters, {len(y)} training row(s) '
                          f'({int((kinds == _REGION).sum())} region, {int(aug.sum())} grayscale-augmented), '
                          f'feature dim {X.shape[1]}.')

        # ---- item-grouped holdout split -------------------------------------
        # A class whose images all come from ONE post can't be assessed:
        # holding that post out leaves the class with no training rows, and
        # keeping it in both sides lets the model score by recognising the
        # post (neighbouring pages), which is not character recognition.
        # Those classes are always trained on and left out of the holdout.
        real_idx = np.where(~aug)[0]
        posts_per_class = defaultdict(set)
        for i in real_idx:
            posts_per_class[y[i]].add(groups[i])
        single_post = sorted(c for c, ps in posts_per_class.items() if len(ps) < 2)
        assessable = [c for c in posts_per_class if c not in set(single_post)]
        if single_post:
            self.stdout.write(self.style.WARNING(
                f'{len(single_post)} class(es) have images from only ONE post and are not assessable '
                f'(trained on, excluded from the holdout): {single_post[:8]}{"..." if len(single_post) > 8 else ""}'
            ))
        assess_idx = real_idx[np.isin(y[real_idx], assessable)]
        n_splits = max(2, int(round(1.0 / options['test_size'])))
        try:
            sgk = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=options['random_state'])
            _tr, te_pos = next(sgk.split(assess_idx, y[assess_idx], groups[assess_idx]))
            test_idx = assess_idx[te_pos]
            held_items = set(groups[test_idx].tolist())
            train_idx = np.where(~np.isin(groups, list(held_items)) | ~np.isin(y, assessable))[0]
        except ValueError as e:
            self.stdout.write(self.style.WARNING(f'Item-grouped split failed ({e}); falling back to a row-level split.'))
            tr_pos, te_pos = train_test_split(np.arange(len(assess_idx)), test_size=options['test_size'],
                                              random_state=options['random_state'], stratify=y[assess_idx])
            test_idx = assess_idx[te_pos]
            train_idx = np.setdiff1d(np.arange(len(y)), test_idx)
        unseen = sorted(set(y[test_idx]) - set(y[train_idx]))
        if unseen:
            self.stdout.write(self.style.WARNING(
                f'{len(unseen)} class(es) have all their rows in the holdout items and can never be predicted: {unseen[:10]}'
            ))

        split_clf = fit_classifier(X[train_idx], y[train_idx], weights[train_idx])
        pred = split_clf.predict(X[test_idx])
        train_acc = float(np.mean(split_clf.predict(X[train_idx][~aug[train_idx]]) == y[train_idx][~aug[train_idx]]))
        correct = pred == y[test_idx]
        test_acc = float(correct.mean())
        solo_mask, region_mask = kinds[test_idx] == _SOLO, kinds[test_idx] == _REGION
        solo_acc = float(correct[solo_mask].mean()) if solo_mask.any() else None
        region_acc = float(correct[region_mask].mean()) if region_mask.any() else None

        # A post often has several images of the same character. Averaging the
        # model's probabilities over a held-out post's solo images shows what
        # classifying ALL of a post's images and combining them would give —
        # much higher than judging one image alone (an image can be a back
        # view, a close-up of a hand, a chibi). Reported separately: row-level
        # accuracy above stays the headline number.
        post_acc = multi_acc = multi_row_acc = n_posts = n_multi = None
        try:
            proba = split_clf.predict_proba(X[test_idx])
            class_pos = {c: k for k, c in enumerate(split_clf.classes_)}
            by_post = defaultdict(list)
            for j, gi in enumerate(test_idx):
                if kinds[gi] == _SOLO:
                    by_post[(groups[gi], y[gi])].append(j)
            post_ok, multi_ok, multi_row_ok = [], [], []
            for (_item, char), js in by_post.items():
                hit = split_clf.classes_[int(np.mean(proba[js], axis=0).argmax())] == char
                post_ok.append(hit)
                if len(js) > 1:
                    multi_ok.append(hit)
                    multi_row_ok += [bool(correct[j]) for j in js]
            if post_ok:
                n_posts, n_multi = len(post_ok), len(multi_ok)
                post_acc = float(np.mean(post_ok))
                multi_acc = float(np.mean(multi_ok)) if multi_ok else None
                multi_row_acc = float(np.mean(multi_row_ok)) if multi_row_ok else None
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Post-level accuracy skipped ({e}).'))

        used_regions = bool((kinds == _REGION).any())
        label = 'solo + manual regions (combined)' if used_regions else 'single-character only'
        self.stdout.write(f'\n[{label}] Train accuracy: {train_acc:.1%}  |  Holdout accuracy: {test_acc:.1%}  '
                          f'(item-grouped split, {len(test_idx)} held-out rows)\n')
        self.stdout.write(
            f'[{label}] Holdout by source — solo: '
            + (f'{solo_acc:.1%} (n={int(solo_mask.sum())})' if solo_acc is not None else 'n/a')
            + ' | region crops: '
            + (f'{region_acc:.1%} (n={int(region_mask.sum())})' if region_acc is not None else 'n/a')
        )
        if post_acc is not None:
            self.stdout.write(
                f'[{label}] Holdout by POST (probabilities averaged over a post\'s solo images) — '
                f'{post_acc:.1%} over {n_posts} post(s)'
                + (f'; the {n_multi} post(s) with 2+ images: {multi_row_acc:.1%} per image -> {multi_acc:.1%} averaged'
                   if multi_acc is not None else '')
            )
        self.stdout.write(f'[{label}] Per-class holdout report:')
        self.stdout.write(classification_report(y[test_idx], pred, zero_division=0))

        final_clf = split_clf
        if not options['no_refit_all']:
            self.stdout.write('Refitting on all rows (holdout included) for the saved model...')
            final_clf = fit_classifier(X, y, weights)

        if classifier_choice == 'logreg':
            default_name = f'character_classifier_{backend_choice}.joblib'
        else:
            default_name = f'character_classifier_{backend_choice}_{feature_source}_{classifier_choice}.joblib'
        output_path = options['output'] or os.path.join(tagger._data_dir(), default_name)
        joblib.dump({
            'classifier': final_clf,
            'classes': list(final_clf.classes_),
            'backend': tagger_backend,
            'general_tag_names': general_tag_names,
            'feature_source': feature_source,
            'feature_dim': int(X.shape[1]),
            'tag_dim': sum(1 for n in (general_tag_names or []) if not n.startswith(('embedding_', 'ccip_'))),
            'min_images': min_images,
            'max_images_per_character': cap,
            'region_weight': region_weight,
            'grayscale_augment': gray,
            'excluded': sorted(exclude),
            'classifier_type': classifier_choice,
            'used_manual_region_labels': used_regions,
            'refit_on_all_rows': not options['no_refit_all'],
            'train_accuracy': train_acc,
            'holdout_accuracy': test_acc,
            'holdout_solo_accuracy': solo_acc,
            'holdout_region_accuracy': region_acc,
            'holdout_post_accuracy': post_acc,
            'holdout_split': 'item-grouped',
        }, output_path)
        self.stdout.write(self.style.SUCCESS(f'\nSaved classifier to {output_path}'))
