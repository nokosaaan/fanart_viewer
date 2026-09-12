"""Trains a supplementary character classifier on this app's OWN labeled
images, for characters the public Danbooru-trained taggers structurally
cannot know about (OCs, characters from very recent/niche titles not yet in
Danbooru's tag vocabulary — see the evaluate_threshold discussion: the
default model has ZERO of a checked recent title's characters in its label
list, and even the larger 'canary' model only has some).

Approach: reuse the existing tagger as a frozen feature extractor (no
retraining of the tagger itself — that would need vastly more data/compute
than a personal archive has) and train a lightweight classifier on top,
scikit-learn LogisticRegression over each image's general-tag probability
vector (from tagger._raw_predict, before any threshold is applied — the
same raw per-tag confidences the tagger already computes, just reused as a
visual feature representation instead of being thresholded into named
tags). This only needs a shallow model to fit, which is fast even on CPU
and has never needed a GPU in any of this app's tooling so far.

v1 scope, deliberately: ONLY single-character items (an item with more than
one confirmed character is skipped entirely) — an image with N characters
would need to be localized per-character first (the same person-detection
crop machinery tagger.py already uses for the Danbooru-trained models) to
avoid the same cross-character feature contamination that motivated that
feature; wiring that up for a from-scratch classifier is a separate, larger
follow-up once this simpler version's accuracy is validated.

A character is only included if it has at least --min-images *single-
character* images total (across however many items that character appears
alone in) — see the `character_image_stats` command to check this ahead of
time. Use --exclude for a label that technically has enough images but
isn't meant to be learned as a class at all — e.g. "any image featuring the
full cast of title X" isn't one character, and training on it just teaches
the classifier to detect "multiple people present" instead of an identity.

A deliberate trait-based bucket (e.g. a "white" label intentionally
covering many DIFFERENT unnamed OCs that just happen to share white hair)
is a different case and should NOT be --exclude'd: it's an intentional
class, not an accidental mislabel, and the classifier's feature vector
(the general-tag probability vector, which already includes hair-color
tags like white_hair) is exactly what such a label needs to be learned
from. Expect lower confidence for that class than for a single-identity
one, since its images vary on everything except the one shared trait —
and if a real, specific character elsewhere in the label set also happens
to have white hair, the two classes can be confused with each other more
than two single-identity classes normally would be.

THE EXPENSIVE PART is feature extraction (running the tagger's forward pass
once per image) — the classifier fit itself is fast. This command caches
every extracted (item_id, character, feature vector, image key) to
--feature-cache after extraction, so a later run that only changes
--exclude/--min-images/--test-size (e.g. to drop a bad label you noticed in
the first report) can reuse it via --use-cache instead of re-extracting from
scratch — but --use-cache trusts the file exactly as saved and never looks
at the DB again at all, so it goes stale the moment a new single-character
item/image is added. A cache is only valid for the --backend/--feature-source
it was extracted with (widening the character set below the cache's
original --min-images floor still needs a fresh extraction — a cache stores
whatever this run's --min-images left in, not a fixed lower floor).

--update-cache is the incremental alternative for ongoing use as the DB
grows: it still scans the DB (needed to discover what's new), but reuses
each image's already-cached feature vector by its own stable key (a
PreviewImage's own id, or the item's id for the legacy single-blob preview
path) instead of re-running the tagger on it — only images that are new (or
weren't in the cache yet) actually get extracted, and the refreshed full set
is written back. The same applies to --include-multi-character's manually
labeled region crops (keyed by item id + image index + box coordinates, so
redrawing a box's coordinates correctly forces that one crop to be
re-extracted). A relabeled/deleted item's stale row is simply dropped
(the DB scan each run only ever keeps what's currently eligible) rather than
reused under its old label.

This is a TRAINING script only — it saves a classifier artifact (joblib
file) but does NOT wire it into the suggestion pipeline (item.tagger /
item.views._suggest_for_item). That integration is a deliberate follow-up
once this command's holdout accuracy has actually been reviewed.

--include-multi-character (v2 extension): also learns from items with 2+
confirmed characters, which v1 skipped entirely — but ONLY from images a
human has manually region-labeled (Item.character_regions, via
RegionLabelQueueManager.jsx/RegionAnnotator.jsx), never from automatic
person-detection alone.

An earlier version of this tried to bridge multi-character items WITHOUT
manual labels via self-training: automatically detect person boxes, accept
the item only when the detected box count happened to equal its confirmed
character count, and pseudo-label each box by asking a "teacher" classifier
(trained on single-character data) which candidate character it most
resembles, keeping only pairings above a confidence threshold. This was
removed as unsound, not just unused: the person detector is the SAME
detector RegionLabelQueueManager's human reviewers already routinely have
to correct (wrong/missing/merged boxes) before a region label is trusted
for training at all — a coincidental box-count match on an unreviewed item
is not evidence those boxes are correct crops of the right people, it's
just an unverified guess with no human ever looking at it. And unlike that
guess, a human-drawn region + label pair already IS a direct, trustworthy
(box, character) mapping — there is nothing left to infer, so there's
nothing for a confidence-gated pseudo-labeling step to add. See
_get_manual_labeled_rows for the actual (much simpler) v2 process: read
Item.character_regions, resolve each region's label (collapsing a linked
CharacterAliasGroup to its canonical name; skipping only the one region
when 2+ unlinked names make it ambiguous — see that method's own
docstring), and train directly on the crop. An item with 2+ characters and
no region labels at all simply contributes nothing here — same as v1 skips
single-character-only, this skips unlabeled-multi-character entirely
rather than guess.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier --min-images 20 --backend canary

  # First run: extract + cache, excluding known-bad labels (accidental
  # mislabels only — NOT an intentional trait bucket like "white", see
  # above)
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー --feature-cache /app/data/tagger/char_features_onnx.joblib

  # Later: tweak and refit WITHOUT re-extracting (never touches the DB)
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --use-cache /app/data/tagger/char_features_onnx.joblib --exclude 牢屋敷メンバー,ユキ

  # Ongoing use as the DB grows: only newly-added images get extracted,
  # everything already in the cache is reused as-is
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --update-cache /app/data/tagger/char_features_onnx.joblib --exclude 牢屋敷メンバー

  # v2: also learn from manually region-labeled multi-character images
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー --include-multi-character
"""
import importlib.util
import os
import time
from collections import defaultdict

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from item.models import Item, CharacterAliasGroup
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

                loss = F.cross_entropy(logits, yb)
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


class Command(BaseCommand):
    help = (
        'Trains a character classifier on this app\'s own single-character-item images, '
        'using the existing tagger as a frozen feature extractor. Training-only — does not '
        'wire the result into the suggestion pipeline.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--min-images', type=int, default=15,
                             help='Minimum single-character images a character needs to be included '
                                  '(default 15 — see character_image_stats to check counts first)')
        parser.add_argument('--max-images-per-character', type=int, default=None,
                             help='Cap how many of each character\'s images actually get extracted '
                                  '(default: no cap). Useful for a quick architecture comparison with the '
                                  "slower --feature-source embedding (canary/timm runs ~6x slower per "
                                  "image than the onnx tag-probability path).")
        parser.add_argument('--exclude', type=str, default='',
                             help='Comma-separated character names to drop entirely, even if they clear '
                                  '--min-images — for labels that are not one visually-coherent character '
                                  '(a catch-all OC bucket, a "whole cast" group tag, etc.)')
        parser.add_argument('--feature-source', choices=['tags', 'embedding'], default='tags',
                             help="'tags' (default): the tagger's own general-tag probability vector "
                                  "(tagger._raw_predict) — works with either --backend. 'embedding': the "
                                  "canary/timm backend's pooled pre-classification-head embedding "
                                  "(tagger.extract_embedding) — retains visual information the tag "
                                  "vocabulary bottleneck already discarded; requires --backend canary.")
        parser.add_argument('--backend', choices=['onnx', 'canary'], default='onnx',
                             help="Tagger backend to use as the frozen feature extractor: 'onnx' (small, "
                                  "always available) or 'canary' (needs INSTALL_TIMM_TAGGER=1). Whichever "
                                  "is chosen, the SAME backend must be used later at inference time to "
                                  "reproduce these exact features — the saved artifact records which one.")
        parser.add_argument('--test-size', type=float, default=0.15,
                             help='Fraction of images per character held out to report accuracy (default 0.15)')
        parser.add_argument('--random-state', type=int, default=42,
                             help='Random seed for the train/holdout split (default 42)')
        parser.add_argument('--output', type=str, default=None,
                             help='Where to save the trained classifier (.joblib). Default: '
                                  "character_classifier_<backend>.joblib under the tagger's own cache dir.")
        parser.add_argument('--feature-cache', type=str, default=None,
                             help='Where to save extracted (item_id, character, feature) data after '
                                  "extraction, for reuse by a later --use-cache run. Default: "
                                  "character_features_<backend>.joblib under the tagger's own cache dir.")
        parser.add_argument('--use-cache', type=str, default=None,
                             help='Path to a previously-saved --feature-cache/--update-cache file — skip DB '
                                  'scanning and feature extraction entirely and refit straight from these '
                                  'cached features, exactly as saved (still applies --exclude/--min-images '
                                  'as filters first). Use this for a quick --exclude/--min-images iteration '
                                  'when you know no new images were added since the cache was made — for '
                                  'ongoing use as the DB grows, prefer --update-cache instead.')
        parser.add_argument('--update-cache', type=str, default=None,
                             help='Path to a --feature-cache/--update-cache file to incrementally refresh: '
                                  'scans the DB as usual, but reuses each already-cached image\'s feature '
                                  'vector instead of re-running the (expensive) tagger forward pass on it, '
                                  "only actually extracting images that are new (or weren't yet successfully "
                                  'cached) since the file was last written. The file is then overwritten '
                                  '(or --feature-cache written instead, if given) with the refreshed full '
                                  'set. If the path does not exist yet, this is just a normal first '
                                  'extraction that creates it. Mutually exclusive with --use-cache (that one '
                                  'skips the DB scan needed to discover what is new).')
        parser.add_argument('--include-multi-character', action='store_true',
                             help='Also learn from multi-character items, but ONLY the ones a human has '
                                  'manually region-labeled (Item.character_regions — see module docstring). '
                                  'Off by default since most archives have few or no labeled regions yet.')
        parser.add_argument('--classifier',
                             choices=['logreg', 'mlp', 'nearest_centroid', 'metric_learning'], default='logreg',
                             help="Classifier head to fit on top of the (frozen) features: 'logreg' "
                                  "(current default, linear), 'mlp' (a small non-linear network, tests "
                                  "whether classifier capacity is the bottleneck), 'nearest_centroid' "
                                  "(a simple metric-learning baseline — classify by cosine similarity to "
                                  "each class's mean feature vector, no gradient training), or "
                                  "'metric_learning' (real gradient-trained metric learning: an ArcFace-style "
                                  "learned embedding projection + margin loss — the literature survey's "
                                  "priority-1 approach; requires torch, see requirements-timm.txt)")

    def handle(self, *args, **options):
        try:
            import joblib
            from sklearn.linear_model import LogisticRegression
            from sklearn.neural_network import MLPClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import classification_report
        except ImportError as e:
            # raise, not self.stderr.write(...); return -- this command runs
            # as a subprocess the GUI panel (see item/classifier_training.py)
            # only learns the outcome of via its exit code; a bare `return`
            # here exits 0 (success) even though nothing was actually
            # trained, which is exactly what made a failed run (e.g.
            # --backend canary with timm not installed) show as "training
            # completed successfully" in the app. Same reasoning applies to
            # every other early-abort `return` in this method below.
            raise CommandError(f'scikit-learn/joblib not available: {e}')

        classifier_choice = options['classifier']

        def make_classifier():
            if classifier_choice == 'mlp':
                # One small hidden layer — with only a few hundred to a
                # couple thousand training rows, a deeper/wider network
                # would just overfit. L2 regularization (alpha) substitutes
                # for early_stopping here — this sklearn version's
                # early_stopping path crashes on string class labels
                # (an internal scoring bug, not specific to this dataset).
                return MLPClassifier(hidden_layer_sizes=(128,), max_iter=500, alpha=1e-2,
                                      random_state=options['random_state'])
            if classifier_choice == 'nearest_centroid':
                return NearestCentroidClassifier()
            if classifier_choice == 'metric_learning':
                return MetricLearningClassifier(random_state=options['random_state'])
            return LogisticRegression(max_iter=2000, class_weight='balanced')

        min_images = options['min_images']
        exclude = {c.strip() for c in options['exclude'].split(',') if c.strip()}
        backend_choice = options['backend']
        feature_source = options['feature_source']
        tagger_backend = 'timm' if backend_choice == 'canary' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            raise CommandError(
                "--backend canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            )
        if feature_source == 'embedding' and backend_choice != 'canary':
            raise CommandError('--feature-source embedding requires --backend canary.')
        if classifier_choice == 'metric_learning' and not _have_torch():
            raise CommandError(
                "--classifier metric_learning requires the optional 'torch' dependency "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg), which is not installed.'
            )

        general_tag_names = None
        use_cache_path = options['use_cache']
        update_cache_path = options['update_cache']
        if use_cache_path and update_cache_path:
            raise CommandError(
                '--use-cache と --update-cache は同時に指定できません(用途が異なります — '
                'どちらか一方を選んでください)。'
            )

        # --use-cache always loads (file must exist, same as before).
        # --update-cache loads only if a file already sits at that path —
        # otherwise this is just a normal first extraction that creates one.
        base_cache = None
        if use_cache_path:
            self.stdout.write(f"Loading cached features from {use_cache_path}...")
            base_cache = joblib.load(use_cache_path)
        elif update_cache_path and os.path.exists(update_cache_path):
            self.stdout.write(f"Loading cached features from {update_cache_path} to reuse where possible...")
            base_cache = joblib.load(update_cache_path)

        if base_cache is not None:
            if base_cache.get('backend') != tagger_backend or base_cache.get('feature_source', 'tags') != feature_source:
                raise CommandError(
                    f"Cache was extracted with backend={base_cache.get('backend')!r}/"
                    f"feature_source={base_cache.get('feature_source', 'tags')!r}, but backend={tagger_backend!r}/"
                    f"feature_source={feature_source!r} was requested — features aren't compatible."
                )
            general_tag_names = base_cache['general_tag_names']

        # Keyed by each row's own stable image_key/region_key (see
        # _extract_features/_get_manual_labeled_rows) so --update-cache can
        # reuse an already-extracted feature by IDENTITY — rows from before
        # that key existed have `None` here and are simply never reused.
        cached_solo_features = {
            key: feat for _item_id, _char, feat, key in (base_cache['rows'] if base_cache else []) if key is not None
        }
        cached_manual_features = {
            key: feat for _char, feat, key in ((base_cache.get('manual_rows') or []) if base_cache else [])
            if key is not None
        }

        if use_cache_path:
            # Normalize pre-image-key cache files (plain 3-tuples) rather
            # than requiring a fresh extraction just to read an old file.
            raw_rows = [r if len(r) == 4 else (*r, None) for r in base_cache['rows']]
            self.stdout.write(f'Loaded {len(raw_rows)} cached (item, character, feature) rows.\n')
        else:
            raw_rows, general_tag_names, n_reused, n_new = self._extract_features(
                min_images, tagger_backend, feature_source, options['max_images_per_character'],
                cached_features=cached_solo_features, expected_general_tag_names=general_tag_names,
            )
            if raw_rows is None:
                # _extract_features already wrote the specific reason via
                # self.stderr before returning None -- this just needs to
                # turn that into a nonzero exit (see the ImportError catch
                # above for why a plain `return` isn't enough).
                raise CommandError('特徴抽出に失敗しました(詳細は上記のログを確認してください)。')
            if cached_solo_features:
                self.stdout.write(f'{n_reused} image(s) reused from cache, {n_new} newly extracted.\n')

        # An item with manual character_regions gets a reliable, human-drawn
        # crop fed straight in below when --include-multi-character is on —
        # including its whole-image feature here too would train on the
        # SAME image/character twice (once on the full frame, once on the
        # region crop), for no benefit, since the crop already supersedes
        # the noisier whole-image signal. Only excluded when that
        # replacement is actually going to run this pass; raw_rows/the
        # feature cache itself stays untouched (deliberately -- see
        # _extract_features' own docstring on staying reusable across
        # differently-flagged runs), this just skips folding those specific
        # rows into X/y.
        annotated_item_ids = (
            set(Item.objects.exclude(character_regions=[]).values_list('id', flat=True))
            if options['include_multi_character'] else set()
        )

        # Apply --exclude as a filter on whatever rows we now have (freshly
        # extracted or loaded from cache) — this is the cheap part, so
        # changing it never requires touching the tagger/DB again.
        # --min-images is applied further below, AFTER manual region rows
        # (if any) are merged in — see that block's own comment for why
        # applying it here, before that merge, would be wrong.
        by_char = defaultdict(list)
        skipped_annotated = 0
        for item_id, char, feature, _image_key in raw_rows:
            if char in exclude:
                continue
            if item_id in annotated_item_ids:
                skipped_annotated += 1
                continue
            by_char[char].append(feature)
        if skipped_annotated:
            self.stdout.write(
                f'Skipped {skipped_annotated} whole-image feature(s) for region-annotated items '
                '(their manually-labeled crop is used instead — see below).'
            )

        # Manual region labels (see Item.character_regions, populated via
        # RegionLabelQueueManager.jsx/RegionAnnotator.jsx) are ground truth
        # — a human already said "this box is character X" — so they're
        # used directly, with no confidence gate or automatic person-
        # detection step of any kind (see module docstring for why an
        # earlier automatic-bootstrap approach was removed: it re-ran the
        # same person detector human reviewers already have to correct, so
        # a coincidental box-count match proved nothing). An item with 2+
        # characters and no region labels at all contributes nothing here
        # — it's skipped, never guessed at.
        #
        # Merged into `by_char` HERE, before --min-images is applied and
        # before the train/test split, rather than tacked onto X_train
        # unconditionally afterward (the previous design): a character that
        # never appears alone — only in region-labeled CP/MULTIPLE images —
        # used to never be counted in the "characters qualify" tally at all
        # (that was computed from solo images only), NOR get any chance to
        # clear --min-images, yet its manual rows still got added to
        # training completely unconditionally regardless of that gate —
        # and, since they were added after the stratified split, NEVER once
        # ended up in the held-out test set either, so the per-class holdout
        # report silently never assessed them. Merging first means the
        # printed tally reflects a character's TRUE total support (solo +
        # manual) and every included class gets a genuine holdout split.
        manual_counts = defaultdict(int)
        # Saved into the cache file below alongside raw_rows whenever this
        # run actually attempted manual-region extraction (see the write
        # block's own comment on why NOT saving an empty/absent list are
        # different things).
        manual_rows = []
        if options['include_multi_character']:
            if use_cache_path and base_cache.get('manual_rows') is not None:
                manual_rows = base_cache['manual_rows']
                self.stdout.write(f'Loaded {len(manual_rows)} cached manually-labeled region(s) (no DB scan).\n')
            else:
                if use_cache_path:
                    self.stdout.write(self.style.WARNING(
                        'このキャッシュには手動領域ラベルの分が含まれていないため、その部分だけDBを'
                        'スキャンして抽出します(単体画像の特徴量はキャッシュのみで済んでいます)。'
                    ))
                manual_rows, n_manual_reused, n_manual_new = self._get_manual_labeled_rows(
                    tagger_backend, options['feature_source'], general_tag_names,
                    cached_features=cached_manual_features,
                )
                if cached_manual_features:
                    self.stdout.write(f'{n_manual_reused} region(s) reused from cache, {n_manual_new} newly extracted.\n')
            for char, feature, _region_key in manual_rows:
                if char in exclude:
                    continue
                by_char[char].append(feature)
                manual_counts[char] += 1
            if not manual_rows:
                self.stdout.write(self.style.WARNING(
                    'No manually-labeled regions found. (See RegionLabelQueueManager.jsx to label multi-character items.)'
                ))

        # Persist the (possibly refreshed) feature set for reuse next time.
        # --use-cache deliberately never writes anything back (it trusts the
        # file exactly as loaded, with no DB check at all); both a plain
        # fresh run and --update-cache always do, so a first run already
        # produces a file --update-cache can build on incrementally later.
        if not use_cache_path:
            default_cache_name = (
                f'character_features_{backend_choice}.joblib' if feature_source == 'tags'
                else f'character_features_{backend_choice}_{feature_source}.joblib'
            )
            feature_cache_path = (
                options['feature_cache'] or update_cache_path
                or os.path.join(tagger._data_dir(), default_cache_name)
            )
            cache_payload = {'rows': raw_rows, 'backend': tagger_backend, 'feature_source': feature_source,
                              'general_tag_names': general_tag_names}
            # Only recorded when this run actually attempted manual-region
            # extraction (--include-multi-character) — an omitted key means
            # "not yet computed, scan the DB if asked for it later", while
            # an empty list means "computed, and there were genuinely none
            # at the time" — conflating the two would make a later
            # --use-cache run silently skip real manual regions that this
            # particular run just never looked for.
            if options['include_multi_character']:
                cache_payload['manual_rows'] = manual_rows
            joblib.dump(cache_payload, feature_cache_path)
            self.stdout.write(self.style.SUCCESS(
                f'\nCached {len(raw_rows)} extracted feature(s)'
                + (f' + {len(manual_rows)} manually-labeled region(s)' if options['include_multi_character'] else '')
                + f' to {feature_cache_path} (reuse with --use-cache to skip re-extraction entirely next time, '
                'or --update-cache to keep it fresh incrementally as the DB grows).\n'
            ))

        eligible = {c: feats for c, feats in by_char.items() if len(feats) >= min_images}
        if len(eligible) < 2:
            raise CommandError(
                f'Only {len(eligible)} character(s) have >= {min_images} images after applying --exclude — '
                'need at least 2 distinct classes to train a classifier.'
            )

        self.stdout.write(f'{len(eligible)} characters included after filtering (>= {min_images} images, '
                           f'excluding {sorted(exclude) or "none"}):')
        for c, feats in sorted(eligible.items(), key=lambda kv: -len(kv[1])):
            manual_n = manual_counts.get(c, 0)
            suffix = f'  ({manual_n} from manual regions)' if manual_n else ''
            self.stdout.write(f'  {len(feats):>5}  {c}{suffix}')

        X = np.stack([f for feats in eligible.values() for f in feats])
        y = np.array([c for c, feats in eligible.items() for _f in feats])

        # Train/holdout split — class_weight='balanced' so the smallest
        # included classes aren't drowned out by the largest ones. Solo and
        # manually-labeled-region rows are already merged into X/y by this
        # point (see above), so every included class — regardless of which
        # source(s) it drew from — gets a genuine, stratified holdout split
        # instead of manual-only classes bypassing it entirely.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=options['test_size'], random_state=options['random_state'], stratify=y,
        )

        def fit_and_report(X_fit, y_fit, label):
            clf = make_classifier()
            clf.fit(X_fit, y_fit)
            train_acc = clf.score(X_fit, y_fit)
            test_acc = clf.score(X_test, y_test)
            self.stdout.write(f'\n[{label}] Train accuracy: {train_acc:.1%}  |  Holdout accuracy: {test_acc:.1%}\n')
            self.stdout.write(f'[{label}] Per-class holdout report:')
            self.stdout.write(classification_report(y_test, clf.predict(X_test), zero_division=0))
            return clf, train_acc, test_acc

        used_manual_regions = bool(manual_counts)
        fit_label = 'solo + manual regions (combined)' if used_manual_regions else 'single-character only'
        final_clf, final_train_acc, final_test_acc = fit_and_report(X_train, y_train, fit_label)

        # Save — self-contained: records which backend/tag ordering produced
        # these features, so a later inference-side integration doesn't have
        # to assume anything matches. Default filename only encodes backend
        # (not classifier_type/feature_source) when both are at their
        # production defaults ('logreg'/'tags') — that's what
        # tagger._load_character_classifier expects to find; any
        # non-default combination gets its own filename so an
        # architecture-comparison run never silently clobbers another.
        if classifier_choice == 'logreg' and feature_source == 'tags':
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
            'min_images': min_images,
            'excluded': sorted(exclude),
            'classifier_type': classifier_choice,
            'used_manual_region_labels': used_manual_regions,
            'train_accuracy': final_train_acc,
            'holdout_accuracy': final_test_acc,
        }, output_path)
        self.stdout.write(self.style.SUCCESS(f'\nSaved classifier to {output_path}'))

    def _get_manual_labeled_rows(self, tagger_backend, feature_source, expected_general_tag_names, cached_features=None):
        """[(character, feature, region_key), ...] for every human-labeled
        region across all items with Item.character_regions set (see
        RegionAnnotator.jsx / ItemViewSet.character_regions_view) — this is
        the ONLY source of multi-character training data (see module
        docstring for why an earlier automatic-detection-based approach was
        removed); a human already drew the box and named it, so there's no
        confidence gate to apply, just direct (character, feature) pairs to
        train on.

        `region_key` identifies the exact crop a feature came from (item id
        + image index + box coordinates) — stable as long as that region's
        box isn't redrawn, so a later --update-cache run can reuse it via
        `cached_features` ({region_key: feature_vector}, see handle()) and
        skip re-running the tagger on it. A region's LABEL is always taken
        fresh from the DB regardless (see the loop below), so relabeling an
        already-cached box's identity still reuses its feature but reflects
        the new label — only the pixels (the box itself) affect this key.

        A region can carry more than one character name for two very
        different reasons: person-detection sometimes merges two
        overlapping people (e.g. a hug pose) into a single box (genuinely
        ambiguous — a crop labeled with 2+ names in that case has no
        single-label identity to teach), OR the same person legitimately
        has 2+ valid names (e.g. a magical girl's real name + transformed
        name) and both were put on the one box that's actually just her.
        CharacterAliasGroup (see its own docstring, and
        CharacterAliasGroupViewSet.candidates / CharacterAliasGroupManager.
        jsx for how a human confirms which case is which) distinguishes
        these: a region whose exact name-set matches a `linked=True` group
        is the second case, so it's trained on using that group's
        alphabetically-first name as the canonical label — an arbitrary
        but consistent choice, since views._expand_character_alias re-
        expands whichever alias the classifier predicts back into the full
        group at inference time anyway. Everything else (2+ names with no
        matching linked group) is still the first case: counted and
        skipped rather than taught as any one name (or, worse, as all of
        them).

        Regions span potentially several of an item's images (see
        Item.character_regions' own docstring) — grouped by image_index per
        item here so each distinct image is only fetched/selected once
        (item.views._select_image_bytes) no matter how many boxes are on
        it, rather than once per region.
        """
        from item.views import _select_image_bytes

        cached_features = cached_features or {}
        linked_groups = {
            tuple(sorted(set(g.characters))): sorted(set(g.characters))
            for g in CharacterAliasGroup.objects.filter(linked=True)
        }

        items = Item.objects.exclude(character_regions=[]).only('id', 'character_regions')
        rows = []
        skipped_mismatch = 0
        skipped_multi_label = 0
        linked_alias_rows = 0
        n_reused = 0
        n_new = 0
        for item in items.iterator():
            regions = item.character_regions or []
            if not regions:
                continue

            by_image = defaultdict(list)
            for region in regions:
                by_image[region.get('image_index')].append(region)

            for image_index, image_regions in by_image.items():
                image_bytes = None  # only fetched on demand (see below) — never needed at all if every region here is already cached
                for region in image_regions:
                    box = region.get('box')
                    names = region.get('characters') or []
                    if not box or not names:
                        continue
                    label = None
                    if len(names) == 1:
                        label = names[0]
                    else:
                        group = linked_groups.get(tuple(sorted(set(names))))
                        if group is not None:
                            label = group[0]  # canonical name — see this method's own docstring
                        else:
                            skipped_multi_label += 1
                            continue

                    region_key = f'{item.id}:{image_index}:{",".join(str(v) for v in box)}'
                    cached_feature = cached_features.get(region_key)
                    if cached_feature is not None:
                        rows.append((label, cached_feature, region_key))
                        n_reused += 1
                        if len(names) > 1:
                            linked_alias_rows += 1
                        continue

                    if image_bytes is None:
                        image_bytes, _resolved_index = _select_image_bytes(item, image_index)
                        if image_bytes is None:
                            self.stderr.write(f'item {item.id}: no image available for its manual regions (image_index={image_index}), skipping')
                            break  # no image means every region on it fails the same way
                    try:
                        crop_bytes = tagger._crop_with_padding(image_bytes, tuple(box))
                        feature, feat_names = self._compute_feature(crop_bytes, tagger_backend, feature_source)
                    except Exception as e:
                        self.stderr.write(f'item {item.id}: manual-region feature extraction failed ({e}), skipping region')
                        continue
                    if feat_names != expected_general_tag_names:
                        skipped_mismatch += 1
                        continue
                    if len(names) > 1:
                        linked_alias_rows += 1
                    n_new += 1
                    rows.append((label, feature, region_key))

        if linked_alias_rows:
            self.stdout.write(f'{linked_alias_rows} region(s) trained via a confirmed CharacterAliasGroup (2+ names, same identity).')
        if skipped_mismatch:
            self.stdout.write(self.style.WARNING(
                f'{skipped_mismatch} manually-labeled region(s) skipped (feature ordering mismatch).'
            ))
        if skipped_multi_label:
            self.stdout.write(self.style.WARNING(
                f'{skipped_multi_label} manually-labeled region(s) skipped (2+ characters on one box — '
                'ambiguous identity, not used for single-label training).'
            ))
        self.stdout.write(f'{len(rows)} manually-labeled region(s) loaded from {items.count()} annotated item(s).')
        return rows, n_reused, n_new

    def _compute_feature(self, image_bytes, tagger_backend, feature_source):
        """(feature_vector, feature_names) for one image, dispatching on
        feature_source. 'embedding' names are placeholders
        ('embedding_0', ...) rather than real tag names — there's nothing
        human-readable to name a pooled embedding dimension, but
        tagger.predict_character's feature-length mismatch guard only
        needs len(feature_names) to match, so a placeholder list of the
        right length is all that's required."""
        if feature_source == 'embedding':
            embedding = tagger.extract_embedding(image_bytes)
            return embedding, [f'embedding_{i}' for i in range(len(embedding))]
        preds, tag_names, _rating_idx, general_idx, _character_idx = tagger._raw_predict(
            image_bytes, None, tagger_backend,
        )
        feature = np.asarray(preds, dtype=np.float32)[general_idx]
        return feature, [tag_names[i] for i in general_idx]

    def _extract_features(self, min_images, tagger_backend, feature_source='tags', max_images_per_character=None,
                           cached_features=None, expected_general_tag_names=None):
        """Scans the DB for single-character items and runs the tagger's
        forward pass once per image (unless already in `cached_features`,
        see below). Returns (rows, general_tag_names, n_reused, n_new) where
        rows is [(item_id, character, feature_vector, image_key), ...] for
        every character with >= min_images single-character images —
        deliberately NOT filtered by --exclude here, so the resulting
        feature-cache file stays maximally reusable for a later run with a
        different --exclude list (excluding is a cheap post-filter, see
        handle()).

        `image_key` is a stable identity for the exact image a row came from
        (a PreviewImage's own id, or `item:<item id>` for the legacy
        single-blob preview path — see below) — independent of this run's
        --min-images/--exclude, and of any OTHER image the same item might
        also have, so a later --update-cache run can look an image up by
        this key regardless of what else changed around it.

        `cached_features`: optional {image_key: feature_vector} from a
        previous run (see handle()'s --update-cache handling) — an eligible
        image whose key is already in here reuses that feature instead of
        re-running the tagger on it. Only images that are genuinely new (or
        weren't successfully cached before) actually get extracted, which is
        the whole point of --update-cache: the DB still has to be scanned to
        discover what's new, but the expensive part (the tagger forward
        pass) is skipped for everything already known.

        `max_images_per_character` caps how many of each character's images
        actually get extracted (first N found, no special sampling) — the
        canary/timm backend runs roughly 6x slower per image than onnx (see
        extract_embedding's docstring), so extracting a character's full
        multi-hundred-image history isn't practical for a quick
        architecture comparison; a capped, smaller-but-still-real sample is
        far more useful than not comparing at all.
        """
        cached_features = cached_features or {}
        by_char = defaultdict(list)  # character name -> [(item_id, image_key, image_bytes), ...]
        items = Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'id', 'characters', 'preview_data',
        )
        for item in items.iterator():
            chars = [c for c in (item.characters or []) if c]
            if len(chars) != 1:
                continue  # v1 scope: single-character items only
            char = chars[0]
            if max_images_per_character is not None and len(by_char[char]) >= max_images_per_character:
                continue
            imgs = list(item.preview_images.all())
            if imgs:
                by_char[char].extend((item.id, f'pi:{img.id}', bytes(img.data)) for img in imgs)
            elif item.preview_data:
                by_char[char].append((item.id, f'item:{item.id}', bytes(item.preview_data)))
            if max_images_per_character is not None:
                by_char[char] = by_char[char][:max_images_per_character]

        eligible = {c: imgs for c, imgs in by_char.items() if len(imgs) >= min_images}
        if len(eligible) < 2:
            self.stderr.write(self.style.ERROR(
                f'Only {len(eligible)} character(s) have >= {min_images} single-character images — '
                'need at least 2 distinct classes to train a classifier. Lower --min-images, or gather '
                'more single-character-item data first (see character_image_stats).'
            ))
            return None, None, 0, 0

        self.stdout.write(f'{len(eligible)} characters qualify (>= {min_images} single-character images each):')
        for c, imgs in sorted(eligible.items(), key=lambda kv: -len(kv[1])):
            self.stdout.write(f'  {len(imgs):>5}  {c}')

        total = sum(len(imgs) for imgs in eligible.values())
        n_cached = sum(1 for imgs in eligible.values() for _iid, key, _b in imgs if key in cached_features)
        self.stdout.write(
            f'\nExtracting features for {total} images (backend={tagger_backend}, feature_source={feature_source})'
            + (f' — {n_cached} already cached, {total - n_cached} to extract...' if cached_features else '...')
        )
        rows = []
        general_tag_names = expected_general_tag_names
        t0 = time.time()
        done = 0
        n_reused = 0
        n_new = 0
        for char, imgs in eligible.items():
            for item_id, image_key, image_bytes in imgs:
                cached_feature = cached_features.get(image_key)
                if cached_feature is not None:
                    rows.append((item_id, char, cached_feature, image_key))
                    n_reused += 1
                    continue
                try:
                    feature, names = self._compute_feature(image_bytes, tagger_backend, feature_source)
                except Exception as e:
                    self.stderr.write(f'item {item_id}: feature extraction failed ({e}), skipping')
                    continue
                if general_tag_names is None:
                    general_tag_names = names
                rows.append((item_id, char, feature, image_key))
                n_new += 1
                done += 1
                if done % 50 == 0 or done == total - n_cached:
                    self.stdout.write(f'  {done}/{total - n_cached} newly extracted ({time.time() - t0:.0f}s elapsed)')

        if len(rows) < 2:
            self.stderr.write(self.style.ERROR('Not enough successfully-extracted features.'))
            return None, None, 0, 0
        return rows, general_tag_names, n_reused, n_new
