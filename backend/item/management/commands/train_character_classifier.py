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
every extracted (item_id, character, feature vector) to --feature-cache
after extraction, so a later run that only changes --exclude/--min-images/
--test-size (e.g. to drop a bad label you noticed in the first report) can
reuse it via --use-cache instead of re-extracting from scratch. A cache is
only valid for the --backend it was extracted with; extracting is still
needed again after adding new single-character items to the DB, or to
widen the character set below the cache's original --min-images floor
(cache stores whatever this run's --min-images left in, not a fixed lower
floor — a much lower --min-images used only for the cache-generating run
maximizes future reuse, at the cost of extracting a few more long-tail
images that run).

This is a TRAINING script only — it saves a classifier artifact (joblib
file) but does NOT wire it into the suggestion pipeline (item.tagger /
item.views._suggest_for_item). That integration is a deliberate follow-up
once this command's holdout accuracy has actually been reviewed.

--include-multi-character (v2 extension): also learns from items with 2+
confirmed characters, which v1 skipped entirely. Since Item.characters is
just a flat name list with no per-region label, there's no direct way to
know which detected person box is which named character — this uses
self-training (pseudo-labeling) to bridge that gap:

  1. Fit a "teacher" classifier on single-character images only (exactly
     v1's process).
  2. For each multi-character item where the person detector finds EXACTLY
     as many boxes as the item has confirmed characters (anything else is
     skipped — an ambiguous box/character count has no reliable
     assignment), score every (box, candidate character) pair with the
     teacher's predict_proba, RESTRICTED to just that item's own confirmed
     characters (never the full class list — the item's cast is already
     known, this only needs to figure out which box is which member of it).
  3. Solve the box<->character assignment as a linear sum assignment
     (scipy) maximizing total confidence, and keep only pairs whose
     confidence clears --bootstrap-confidence — a low-confidence pairing is
     as likely to be wrong as right, and a wrong pseudo-label actively
     teaches the wrong thing.
  4. Retrain a final classifier on single-character data PLUS the accepted
     pseudo-labeled crops, but still report holdout accuracy against ONLY
     the original single-character holdout split (never against
     pseudo-labeled data) — the whole point is measuring whether the extra
     (noisier) data helps the model recognize real, unambiguous examples
     better, not measuring how well it reproduces its own guesses.

This is the same "reuse the existing per-person crop machinery, tag each
crop independently" strategy tagger.py's suggest_tags() already uses for
the Danbooru-trained backends (see its docstring) — applied here to
generate labeled TRAINING data instead of a live suggestion.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier --min-images 20 --backend canary

  # First run: extract + cache, excluding known-bad labels (accidental
  # mislabels only — NOT an intentional trait bucket like "white", see
  # above)
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー --feature-cache /app/data/tagger/char_features_onnx.joblib

  # Later: tweak and refit WITHOUT re-extracting
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --use-cache /app/data/tagger/char_features_onnx.joblib --exclude 牢屋敷メンバー,ユキ

  # v2: also bootstrap-learn from multi-character images
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー --include-multi-character --bootstrap-confidence 0.7
"""
import importlib.util
import os
import time
from collections import defaultdict

import numpy as np
from django.core.management.base import BaseCommand

from item.models import Item
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
                             help='Path to a previously-saved --feature-cache file — skip DB scanning and '
                                  'feature extraction entirely and refit straight from these cached '
                                  'features (still applies --exclude/--min-images as filters first).')
        parser.add_argument('--include-multi-character', action='store_true',
                             help='Also bootstrap-learn from multi-character items via person-detection '
                                  'crops + self-training (see module docstring). Off by default since it '
                                  'adds a second, separately-cached extraction pass.')
        parser.add_argument('--bootstrap-confidence', type=float, default=0.7,
                             help="Minimum teacher-classifier confidence for a crop<->character pseudo-"
                                  "label to be accepted (default 0.7 — stricter than the production "
                                  "0.5 default, since a wrong pseudo-label actively teaches the wrong thing)")
        parser.add_argument('--max-characters-per-item', type=int, default=6,
                             help='Skip multi-character items with more confirmed characters than this '
                                  '(default 6) — a large group shot is unlikely to get a clean 1:1 '
                                  'person-detection match anyway, and each extra crop costs a tagger pass.')
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
        parser.add_argument('--multi-feature-cache', type=str, default=None,
                             help='Where to cache extracted multi-character crop features (raw, before '
                                  'pseudo-labeling). Default: character_features_multi_<backend>.joblib.')
        parser.add_argument('--use-multi-cache', type=str, default=None,
                             help='Path to a previously-saved --multi-feature-cache file — skip person '
                                  'detection/crop extraction and pseudo-label straight from these.')

    def handle(self, *args, **options):
        try:
            import joblib
            from sklearn.linear_model import LogisticRegression
            from sklearn.neural_network import MLPClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import classification_report
        except ImportError as e:
            self.stderr.write(self.style.ERROR(f'scikit-learn/joblib not available: {e}'))
            return

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
            self.stderr.write(self.style.ERROR(
                "--backend canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            ))
            return
        if feature_source == 'embedding' and backend_choice != 'canary':
            self.stderr.write(self.style.ERROR('--feature-source embedding requires --backend canary.'))
            return
        if classifier_choice == 'metric_learning' and not _have_torch():
            self.stderr.write(self.style.ERROR(
                "--classifier metric_learning requires the optional 'torch' dependency "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg), which is not installed.'
            ))
            return

        general_tag_names = None
        if options['use_cache']:
            self.stdout.write(f"Loading cached features from {options['use_cache']}...")
            cache = joblib.load(options['use_cache'])
            if cache.get('backend') != tagger_backend or cache.get('feature_source', 'tags') != feature_source:
                self.stderr.write(self.style.ERROR(
                    f"Cache was extracted with backend={cache.get('backend')!r}/"
                    f"feature_source={cache.get('feature_source', 'tags')!r}, but backend={tagger_backend!r}/"
                    f"feature_source={feature_source!r} was requested — features aren't compatible."
                ))
                return
            raw_rows = cache['rows']  # [(item_id, character, feature_vector), ...]
            general_tag_names = cache['general_tag_names']
            self.stdout.write(f'Loaded {len(raw_rows)} cached (item, character, feature) rows.\n')
        else:
            raw_rows, general_tag_names = self._extract_features(
                min_images, tagger_backend, feature_source, options['max_images_per_character'],
            )
            if raw_rows is None:
                return
            default_cache_name = (
                f'character_features_{backend_choice}.joblib' if feature_source == 'tags'
                else f'character_features_{backend_choice}_{feature_source}.joblib'
            )
            feature_cache_path = options['feature_cache'] or os.path.join(tagger._data_dir(), default_cache_name)
            joblib.dump({'rows': raw_rows, 'backend': tagger_backend, 'feature_source': feature_source,
                         'general_tag_names': general_tag_names},
                        feature_cache_path)
            self.stdout.write(self.style.SUCCESS(
                f'\nCached {len(raw_rows)} extracted features to {feature_cache_path} '
                '(reuse with --use-cache to skip re-extraction next time).\n'
            ))

        # Apply --exclude and --min-images as filters on whatever rows we now have
        # (freshly extracted or loaded from cache) — this is the cheap part, so
        # changing these two never requires touching the tagger/DB again.
        by_char = defaultdict(list)
        for item_id, char, feature in raw_rows:
            if char in exclude:
                continue
            by_char[char].append((item_id, feature))
        eligible = {c: rows for c, rows in by_char.items() if len(rows) >= min_images}
        if len(eligible) < 2:
            self.stderr.write(self.style.ERROR(
                f'Only {len(eligible)} character(s) have >= {min_images} images after applying --exclude — '
                'need at least 2 distinct classes to train a classifier.'
            ))
            return

        self.stdout.write(f'{len(eligible)} characters included after filtering (>= {min_images} images, '
                           f'excluding {sorted(exclude) or "none"}):')
        for c, rows in sorted(eligible.items(), key=lambda kv: -len(kv[1])):
            self.stdout.write(f'  {len(rows):>5}  {c}')

        X = np.stack([f for rows in eligible.values() for _id, f in rows])
        y = np.array([c for c, rows in eligible.items() for _row in rows])

        # Train/holdout split — class_weight='balanced' so the smallest
        # included classes aren't drowned out by the largest ones. This
        # holdout is the ONE evaluation ground truth used throughout,
        # including after --include-multi-character adds bootstrap data —
        # never evaluated against pseudo-labels.
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

        teacher, final_train_acc, final_test_acc = fit_and_report(X_train, y_train, 'single-character only')
        final_clf = teacher
        used_bootstrap = False

        if options['include_multi_character']:
            multi_rows = self._get_multi_character_rows(options, tagger_backend, general_tag_names)
            pseudo_rows = self._bootstrap_label(
                multi_rows, teacher, options['bootstrap_confidence'],
            )
            if pseudo_rows:
                X_boot = np.stack([f for _c, f in pseudo_rows])
                y_boot = np.array([c for c, _f in pseudo_rows])
                X_combined = np.concatenate([X_train, X_boot])
                y_combined = np.concatenate([y_train, y_boot])
                self.stdout.write(
                    f'\nAdding {len(pseudo_rows)} bootstrap-labeled crops to the '
                    f'{len(X_train)} single-character training examples...'
                )
                teacher_test_acc = final_test_acc
                final_clf, final_train_acc, final_test_acc = fit_and_report(
                    X_combined, y_combined, 'single-character + bootstrap',
                )
                used_bootstrap = True
                self.stdout.write(self.style.SUCCESS(
                    f'\nHoldout accuracy: {teacher_test_acc:.1%} (single-character only) -> '
                    f'{final_test_acc:.1%} (with bootstrap crops)'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    '\nNo multi-character crops cleared --bootstrap-confidence — keeping the '
                    'single-character-only classifier.'
                ))

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
            'used_multi_character_bootstrap': used_bootstrap,
            'train_accuracy': final_train_acc,
            'holdout_accuracy': final_test_acc,
        }, output_path)
        self.stdout.write(self.style.SUCCESS(f'\nSaved classifier to {output_path}'))

    def _get_multi_character_rows(self, options, tagger_backend, expected_general_tag_names):
        """Returns [(item_id, candidate_chars, [crop_feature, ...]), ...] for
        multi-character items where person detection found EXACTLY as many
        boxes as the item has confirmed characters — anything else (0/1
        boxes, or a mismatched count) is skipped, since there's no reliable
        way to know which box is which character otherwise. Raw and
        unlabeled — pairing crops to specific character names happens in
        _bootstrap_label, using a teacher classifier that isn't fit yet
        when this runs."""
        import joblib

        backend_choice = options['backend']
        feature_source = options['feature_source']
        if options['use_multi_cache']:
            self.stdout.write(f"Loading cached multi-character crops from {options['use_multi_cache']}...")
            cache = joblib.load(options['use_multi_cache'])
            if cache.get('backend') != tagger_backend or cache.get('feature_source', 'tags') != feature_source:
                self.stderr.write(self.style.ERROR(
                    f"Multi-character cache was extracted with backend={cache.get('backend')!r}/"
                    f"feature_source={cache.get('feature_source', 'tags')!r}, but backend={tagger_backend!r}/"
                    f"feature_source={feature_source!r} was requested."
                ))
                return []
            self.stdout.write(f"Loaded {len(cache['rows'])} cached multi-character items.\n")
            return cache['rows']

        max_chars = options['max_characters_per_item']
        items = Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'id', 'characters', 'preview_data',
        )
        candidates = []  # (item_id, chars, image_bytes)
        for item in items.iterator():
            chars = [c for c in (item.characters or []) if c]
            if not (2 <= len(chars) <= max_chars):
                continue
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                continue
            candidates.append((item.id, chars, image_bytes))

        self.stdout.write(f'\n{len(candidates)} multi-character items to check for a clean person-detection match...')
        rows = []
        t0 = time.time()
        for i, (item_id, chars, image_bytes) in enumerate(candidates):
            try:
                boxes = tagger._detect_person_boxes(image_bytes)
            except Exception as e:
                self.stderr.write(f'item {item_id}: person detection failed ({e}), skipping')
                continue
            if len(boxes) != len(chars):
                continue  # ambiguous — can't assign crops to characters reliably

            crop_features = []
            ok = True
            for box in boxes:
                try:
                    crop_bytes = tagger._crop_with_padding(image_bytes, box)
                    feature, names = self._compute_feature(crop_bytes, tagger_backend, options['feature_source'])
                except Exception as e:
                    self.stderr.write(f'item {item_id}: crop feature extraction failed ({e}), skipping item')
                    ok = False
                    break
                if names != expected_general_tag_names:
                    self.stderr.write(f'item {item_id}: feature ordering mismatch, skipping item')
                    ok = False
                    break
                crop_features.append(feature)
            if ok and crop_features:
                rows.append((item_id, chars, crop_features))
            if (i + 1) % 25 == 0 or i + 1 == len(candidates):
                self.stdout.write(f'  checked {i + 1}/{len(candidates)} items, {len(rows)} usable so far '
                                   f'({time.time() - t0:.0f}s elapsed)')

        default_multi_name = (
            f'character_features_multi_{backend_choice}.joblib' if feature_source == 'tags'
            else f'character_features_multi_{backend_choice}_{feature_source}.joblib'
        )
        multi_cache_path = options['multi_feature_cache'] or os.path.join(tagger._data_dir(), default_multi_name)
        joblib.dump({'rows': rows, 'backend': tagger_backend, 'feature_source': feature_source}, multi_cache_path)
        self.stdout.write(self.style.SUCCESS(
            f'\n{len(rows)}/{len(candidates)} multi-character items had a clean box<->character-count match. '
            f'Cached to {multi_cache_path}.\n'
        ))
        return rows

    def _bootstrap_label(self, multi_rows, teacher, min_confidence):
        """For each (item_id, candidate_chars, crop_features), scores every
        (crop, candidate character) pair with the teacher's predict_proba
        RESTRICTED to just that item's own candidate_chars (never the full
        class list — an item's cast is already known; this only resolves
        which crop is which member of it), solves the assignment as a
        linear sum assignment maximizing total confidence (scipy), and
        keeps only pairs clearing min_confidence. Items whose candidate
        characters aren't ALL in the teacher's known classes are skipped
        entirely (can't validate against an unknown class). Returns
        [(character, feature), ...] pseudo-labeled rows.
        """
        from scipy.optimize import linear_sum_assignment

        class_index = {c: i for i, c in enumerate(teacher.classes_)}
        accepted = []
        skipped_unknown_class = 0

        for item_id, chars, crop_features in multi_rows:
            if not all(c in class_index for c in chars):
                skipped_unknown_class += 1
                continue

            X_crops = np.stack(crop_features)
            full_proba = teacher.predict_proba(X_crops)  # (n_crops, n_classes)
            col_idx = [class_index[c] for c in chars]
            restricted = full_proba[:, col_idx]  # (n_crops, n_chars) — same order as `chars`

            row_ind, col_ind = linear_sum_assignment(-restricted)  # maximize confidence
            for r, c in zip(row_ind, col_ind):
                confidence = restricted[r, c]
                if confidence >= min_confidence:
                    accepted.append((chars[c], crop_features[r]))

        self.stdout.write(
            f'Bootstrap: {len(accepted)} crop<->character pairs accepted (>= {min_confidence:.0%} confidence) '
            f'out of {sum(len(r[2]) for r in multi_rows)} candidate crops across {len(multi_rows)} items '
            f'({skipped_unknown_class} items skipped — a confirmed character isn\'t in the trained class set).'
        )
        return accepted

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

    def _extract_features(self, min_images, tagger_backend, feature_source='tags', max_images_per_character=None):
        """Scans the DB for single-character items and runs the tagger's
        forward pass once per image. Returns (rows, general_tag_names) where
        rows is [(item_id, character, feature_vector), ...] for every
        character with >= min_images single-character images — deliberately
        NOT filtered by --exclude here, so the resulting feature-cache file
        stays maximally reusable for a later run with a different --exclude
        list (excluding is a cheap post-filter, see handle()).

        `max_images_per_character` caps how many of each character's images
        actually get extracted (first N found, no special sampling) — the
        canary/timm backend runs roughly 6x slower per image than onnx (see
        extract_embedding's docstring), so extracting a character's full
        multi-hundred-image history isn't practical for a quick
        architecture comparison; a capped, smaller-but-still-real sample is
        far more useful than not comparing at all.
        """
        by_char = defaultdict(list)  # character name -> [(item_id, image_bytes), ...]
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
                by_char[char].extend((item.id, bytes(img.data)) for img in imgs)
            elif item.preview_data:
                by_char[char].append((item.id, bytes(item.preview_data)))
            if max_images_per_character is not None:
                by_char[char] = by_char[char][:max_images_per_character]

        eligible = {c: imgs for c, imgs in by_char.items() if len(imgs) >= min_images}
        if len(eligible) < 2:
            self.stderr.write(self.style.ERROR(
                f'Only {len(eligible)} character(s) have >= {min_images} single-character images — '
                'need at least 2 distinct classes to train a classifier. Lower --min-images, or gather '
                'more single-character-item data first (see character_image_stats).'
            ))
            return None, None

        self.stdout.write(f'{len(eligible)} characters qualify (>= {min_images} single-character images each):')
        for c, imgs in sorted(eligible.items(), key=lambda kv: -len(kv[1])):
            self.stdout.write(f'  {len(imgs):>5}  {c}')

        total = sum(len(imgs) for imgs in eligible.values())
        self.stdout.write(f'\nExtracting features for {total} images '
                           f'(backend={tagger_backend}, feature_source={feature_source})...')
        rows = []
        general_tag_names = None
        t0 = time.time()
        done = 0
        for char, imgs in eligible.items():
            for item_id, image_bytes in imgs:
                try:
                    feature, names = self._compute_feature(image_bytes, tagger_backend, feature_source)
                except Exception as e:
                    self.stderr.write(f'item {item_id}: feature extraction failed ({e}), skipping')
                    continue
                if general_tag_names is None:
                    general_tag_names = names
                rows.append((item_id, char, feature))
                done += 1
                if done % 50 == 0 or done == total:
                    self.stdout.write(f'  {done}/{total} ({time.time() - t0:.0f}s elapsed)')

        if len(rows) < 2:
            self.stderr.write(self.style.ERROR('Not enough successfully-extracted features.'))
            return None, None
        return rows, general_tag_names
