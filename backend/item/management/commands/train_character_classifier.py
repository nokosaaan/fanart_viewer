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
once per image, over every single-character image, every manually-labeled
region, and — with --include-multi-character — every multi-character
bootstrap crop too) — the classifier fit itself is fast. All THREE
extraction passes are backed by a SQLite cache (FeatureCache /
RegionFeatureCache / MultiFeatureCache, see their own docstrings) that is
ALWAYS on (no flag needed) and ALWAYS resumed from: every successfully-
extracted image/crop is committed to disk immediately, so killing the
process partway through (a power loss, an OOM kill, `docker compose down`,
anything) loses at most the one image that was in flight — the next
invocation just picks up where it left off, skipping everything already
cached. This is also how adding new single-character items, new manually-
labeled regions, or new multi-character items to the DB gets picked up
cheaply later: from the cache's point of view a "resume" and "extract the
newly-added items" look identical, so no separate mechanism is needed for
either. RegionFeatureCache specifically caches only the crop's feature —
never the resolved label — since a region's label can change over time
(a human edits it, or links/unlinks a CharacterAliasGroup) independently
of the box's pixel content; label resolution is always re-read fresh from
the DB, so relinking an alias group takes effect immediately with no
re-extraction needed. A cache is only valid for the --backend/
--feature-source it was created with (checked on open, hard error on
mismatch); it does NOT need to be regenerated just to widen the character
set below its original --min-images floor — --min-images is applied as a
cheap post-filter on top of whatever's cached, not baked into the cache
itself. A sibling `.lock` file (held for the process's whole lifetime)
stops two training runs from writing the same cache at once.

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

  # First run: extract + cache (the SQLite cache is always created —
  # excluding known-bad labels here is about accidental mislabels only,
  # NOT an intentional trait bucket like "white", see above)
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー

  # Later: tweak --exclude/--min-images/--test-size and refit — already-
  # extracted images are reused automatically from the cache, no flag
  # needed (and if the previous run was killed partway, this also just
  # resumes it instead of starting over)
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー,ユキ

  # v2: also bootstrap-learn from multi-character images
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude 牢屋敷メンバー --include-multi-character --bootstrap-confidence 0.7
"""
import fcntl
import importlib.util
import json
import os
import sqlite3
import time
from collections import defaultdict

import numpy as np
from django.core.management.base import BaseCommand

from item.models import Item, CharacterAliasGroup
from item import tagger


def _have_torch():
    # Same lazy find_spec check tagger.py uses for HAVE_TIMM — avoids
    # importing torch (and paying its import cost) just to check whether
    # --classifier metric_learning is even usable.
    return importlib.util.find_spec('torch') is not None


class _CacheLockedError(RuntimeError):
    """Another process already holds the lock on a feature-cache file (see
    _acquire_lock) — surfaced as a clean error message rather than an
    opaque OSError."""


def _acquire_lock(cache_path):
    """Exclusive, non-blocking advisory lock on a sibling `.lock` file next
    to `cache_path` — held for this process's entire lifetime (never
    unlocked explicitly; released automatically when the file handle is
    garbage-collected/the process exits). Guards against two
    train_character_classifier runs writing the same SQLite cache at once,
    which SQLite itself does not safely support for concurrent writers
    without WAL-mode tuning this command doesn't otherwise need.

    Returns the open file handle (caller must keep a reference — closing
    or dropping it releases the lock) or raises _CacheLockedError.
    """
    lock_fh = open(cache_path + '.lock', 'w')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fh.close()
        raise _CacheLockedError(
            f'Another train_character_classifier run already holds the lock on {cache_path} '
            '— wait for it to finish, or pass a different cache path for this run.'
        )
    return lock_fh


class FeatureCache:
    """SQLite-backed, resumable cache of (item_id, img_order) -> (character,
    feature vector) for _extract_features's single-character extraction
    pass. Always created (no opt-in flag needed — see the module docstring)
    and always resumed from: a row is committed to disk immediately after
    each image's feature is successfully extracted, so a crash (power loss,
    OOM kill, etc.) partway through a multi-hour extraction run loses at
    most the one image that was in flight, not the whole run — the next
    invocation just skips every (item_id, img_order) pair already present
    and extracts the rest. This is also how adding new single-character
    items to the DB gets picked up cheaply later: it looks identical to a
    "resume" from this cache's point of view (some images are new, i.e.
    not yet cached), so no separate mechanism is needed for that case.

    `item_id` alone is NOT unique — one item can contribute multiple images
    (item.preview_images), so `img_order` (PreviewImage.order, or -1 for
    the single legacy item.preview_data image) disambiguates.

    `legacy_joblib_path`: this cache replaced an older, non-resumable
    single-blob .joblib cache (same command, before this SQLite rewrite —
    see the module docstring's history). If one already exists on disk
    from an earlier training run (e.g. an onnx cache built before this
    change, while a canary cache gets built fresh under the new code) and
    this SQLite cache is brand new (0 rows), its rows are imported once on
    open so that work isn't thrown away — the next extraction pass then
    only needs to fill in whatever's genuinely missing, same as any other
    resume. Silently ignored if the path doesn't exist or its own stored
    backend/feature_source don't match (never blocks startup).
    """

    def __init__(self, path, backend, feature_source, legacy_joblib_path=None):
        self.path = path
        self._lock_fh = _acquire_lock(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS features (
            item_id INTEGER NOT NULL, img_order INTEGER NOT NULL,
            character TEXT NOT NULL, feature BLOB NOT NULL,
            PRIMARY KEY (item_id, img_order)
        )''')
        self.conn.commit()
        self._validate_or_init_meta(backend, feature_source)
        if legacy_joblib_path and self.count() == 0:
            self._import_legacy_joblib(legacy_joblib_path, backend, feature_source)

    def _import_legacy_joblib(self, legacy_path, backend, feature_source):
        if not os.path.exists(legacy_path):
            return
        import joblib
        try:
            cache = joblib.load(legacy_path)
        except Exception:
            return  # unreadable/corrupt — treat as absent, a fresh extraction still works
        if cache.get('backend') != backend or cache.get('feature_source', 'tags') != feature_source:
            return  # not compatible with this run — never seen before, from this cache's POV
        rows = cache.get('rows') or []
        if not rows:
            return
        names = cache.get('general_tag_names')
        if names is not None:
            self.set_general_tag_names(names)
        # The old format never recorded which image within an item a row
        # came from (item_id alone, not (item_id, img_order)) — reproduce
        # a stable per-item index by counting duplicates in the order they
        # appear, which matches real PreviewImage.order in practice (both
        # were populated by iterating item.preview_images in the same
        # order). Worst case on a mismatch is one redundant re-extraction
        # later, never lost or incorrect training data.
        next_order = defaultdict(int)
        for item_id, character, feature in rows:
            img_order = next_order[item_id]
            next_order[item_id] += 1
            self.add(item_id, img_order, character, feature)
        self.conn.commit()
        print(f'Imported {len(rows)} row(s) from the legacy cache {legacy_path} into {self.path}.')

    def _meta_get(self, key):
        row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key, value):
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, value))
        self.conn.commit()

    def _validate_or_init_meta(self, backend, feature_source):
        cached_backend = self._meta_get('backend')
        if cached_backend is None:
            self._meta_set('backend', backend)
            self._meta_set('feature_source', feature_source)
            return
        cached_fs = self._meta_get('feature_source')
        if cached_backend != backend or cached_fs != feature_source:
            raise RuntimeError(
                f'{self.path} was created with backend={cached_backend!r}/feature_source={cached_fs!r}, '
                f'but backend={backend!r}/feature_source={feature_source!r} was requested this run — '
                'features are not compatible. Use a different --feature-cache path, or delete the old file '
                'if you meant to start over.'
            )

    def get_general_tag_names(self):
        raw = self._meta_get('general_tag_names')
        return json.loads(raw) if raw is not None else None

    def set_general_tag_names(self, names):
        self._meta_set('general_tag_names', json.dumps(list(names)))

    def has(self, item_id, img_order):
        return self.conn.execute(
            'SELECT 1 FROM features WHERE item_id=? AND img_order=?', (item_id, img_order),
        ).fetchone() is not None

    def add(self, item_id, img_order, character, feature):
        self.conn.execute(
            'INSERT OR REPLACE INTO features VALUES (?, ?, ?, ?)',
            (item_id, img_order, character, np.asarray(feature, dtype=np.float32).tobytes()),
        )
        self.conn.commit()  # commit per-row: a crash loses at most this one insert

    def all_rows(self):
        """[(item_id, character, feature_vector), ...] — same shape the
        rest of this command has always worked with."""
        return [
            (item_id, character, np.frombuffer(blob, dtype=np.float32))
            for item_id, character, blob in self.conn.execute(
                'SELECT item_id, character, feature FROM features'
            )
        ]

    def count(self):
        return self.conn.execute('SELECT COUNT(*) FROM features').fetchone()[0]


class MultiFeatureCache:
    """SQLite-backed, resumable cache for _get_multi_character_rows's
    per-item person-detection + crop-feature-extraction pass. One row per
    item_id (unlike FeatureCache, an item is checked exactly once here
    regardless of how many people it contains) — `crop_count=0` marks an
    item that was checked and deterministically found NOT to have a clean
    box<->character-count match (a stable fact that will never change on
    retry, so it's cached too and never re-checked), while `crop_count>0`
    stores the actual extracted crop features. An exception during
    detection/cropping/feature-extraction is NOT cached either way — those
    are treated as transient and simply retried on the next run.

    `legacy_joblib_path`: same one-time import as FeatureCache's own
    parameter, for a pre-SQLite-rewrite .joblib multi-character cache.
    Only ever recorded "usable" items (crop_count>0 equivalent) in the old
    format, never the "checked, not a clean match" ones — so migrating one
    in means every previously-skipped item gets re-checked once more (a
    person-detection pass, not a full tagger forward pass per box; cheap
    relative to what's being saved).
    """

    def __init__(self, path, backend, feature_source, legacy_joblib_path=None):
        self.path = path
        self._lock_fh = _acquire_lock(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS multi_features (
            item_id INTEGER PRIMARY KEY, chars TEXT NOT NULL,
            crop_count INTEGER NOT NULL, crops BLOB
        )''')
        self.conn.commit()
        self._validate_or_init_meta(backend, feature_source)
        if legacy_joblib_path and self.count_checked() == 0:
            self._import_legacy_joblib(legacy_joblib_path, backend, feature_source)

    def _import_legacy_joblib(self, legacy_path, backend, feature_source):
        if not os.path.exists(legacy_path):
            return
        import joblib
        try:
            cache = joblib.load(legacy_path)
        except Exception:
            return
        if cache.get('backend') != backend or cache.get('feature_source', 'tags') != feature_source:
            return
        rows = cache.get('rows') or []
        if not rows:
            return
        for item_id, chars, crop_features in rows:
            self.add(item_id, chars, crop_features)
        print(f'Imported {len(rows)} row(s) from the legacy cache {legacy_path} into {self.path}.')

    def _meta_get(self, key):
        row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key, value):
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, value))
        self.conn.commit()

    def _validate_or_init_meta(self, backend, feature_source):
        cached_backend = self._meta_get('backend')
        if cached_backend is None:
            self._meta_set('backend', backend)
            self._meta_set('feature_source', feature_source)
            return
        cached_fs = self._meta_get('feature_source')
        if cached_backend != backend or cached_fs != feature_source:
            raise RuntimeError(
                f'{self.path} was created with backend={cached_backend!r}/feature_source={cached_fs!r}, '
                f'but backend={backend!r}/feature_source={feature_source!r} was requested this run. '
                'Use a different --multi-feature-cache path, or delete the old file if you meant to start over.'
            )

    def has(self, item_id):
        return self.conn.execute(
            'SELECT 1 FROM multi_features WHERE item_id=?', (item_id,),
        ).fetchone() is not None

    def add_unusable(self, item_id):
        self.conn.execute(
            'INSERT OR REPLACE INTO multi_features VALUES (?, ?, 0, NULL)', (item_id, '[]'),
        )
        self.conn.commit()

    def add(self, item_id, chars, crop_features):
        stacked = np.stack([np.asarray(f, dtype=np.float32) for f in crop_features])
        self.conn.execute(
            'INSERT OR REPLACE INTO multi_features VALUES (?, ?, ?, ?)',
            (item_id, json.dumps(list(chars)), len(crop_features), stacked.tobytes()),
        )
        self.conn.commit()

    def usable_rows(self):
        """[(item_id, chars, [feature_vector, ...]), ...] — same shape
        _get_multi_character_rows has always returned, skipping the
        crop_count=0 (deterministically unusable) rows."""
        rows = []
        for item_id, chars_json, crop_count, blob in self.conn.execute(
            'SELECT item_id, chars, crop_count, crops FROM multi_features WHERE crop_count > 0'
        ):
            chars = json.loads(chars_json)
            flat = np.frombuffer(blob, dtype=np.float32)
            crops = list(flat.reshape(crop_count, -1))
            rows.append((item_id, chars, crops))
        return rows

    def count_checked(self):
        return self.conn.execute('SELECT COUNT(*) FROM multi_features').fetchone()[0]

    def count_usable(self):
        return self.conn.execute(
            'SELECT COUNT(*) FROM multi_features WHERE crop_count > 0'
        ).fetchone()[0]


class RegionFeatureCache:
    """SQLite-backed, resumable cache of manually-labeled-region crop
    features for _get_manual_labeled_rows — same always-on, always-
    resumed, per-row-committed design as FeatureCache/MultiFeatureCache.

    Deliberately caches ONLY the crop's feature vector, keyed on
    (item_id, image_index, box) — NOT the resolved label. A region's
    `characters` list (and therefore its label, via CharacterAliasGroup)
    is cheap to re-read from Item.character_regions directly and can
    change over time (a human edits a region, or links/unlinks an alias
    group) independently of the box's pixel content — re-deriving the
    label fresh every run from the live DB avoids ever serving a stale
    label from a cache that has no way to know a group was just linked.
    Only the expensive part (crop + tagger forward pass) is skipped on a
    cache hit.

    `image_index` is normalized to -1 for the legacy single-image
    fallback (Item.character_regions stores None there — see its own
    docstring) since SQLite treats NULL as never equal to itself in a
    PRIMARY KEY, which would silently defeat deduplication.
    `box` is normalized to a tuple of rounded ints before being JSON-
    encoded into the key, so int/float storage quirks in the same
    logical box don't cause spurious cache misses.
    """

    def __init__(self, path, backend, feature_source):
        self.path = path
        self._lock_fh = _acquire_lock(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS region_features (
            item_id INTEGER NOT NULL, image_index INTEGER NOT NULL, box TEXT NOT NULL,
            feature BLOB NOT NULL,
            PRIMARY KEY (item_id, image_index, box)
        )''')
        self.conn.commit()
        self._validate_or_init_meta(backend, feature_source)

    def _meta_get(self, key):
        row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key, value):
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, value))
        self.conn.commit()

    def _validate_or_init_meta(self, backend, feature_source):
        cached_backend = self._meta_get('backend')
        if cached_backend is None:
            self._meta_set('backend', backend)
            self._meta_set('feature_source', feature_source)
            return
        cached_fs = self._meta_get('feature_source')
        if cached_backend != backend or cached_fs != feature_source:
            raise RuntimeError(
                f'{self.path} was created with backend={cached_backend!r}/feature_source={cached_fs!r}, '
                f'but backend={backend!r}/feature_source={feature_source!r} was requested this run. '
                'Use a different --region-feature-cache path, or delete the old file if you meant to start over.'
            )

    @staticmethod
    def _key(item_id, image_index, box):
        idx = -1 if image_index is None else image_index
        box_key = json.dumps([round(float(v)) for v in box])
        return item_id, idx, box_key

    def has(self, item_id, image_index, box):
        item_id, idx, box_key = self._key(item_id, image_index, box)
        return self.conn.execute(
            'SELECT 1 FROM region_features WHERE item_id=? AND image_index=? AND box=?',
            (item_id, idx, box_key),
        ).fetchone() is not None

    def get(self, item_id, image_index, box):
        item_id, idx, box_key = self._key(item_id, image_index, box)
        row = self.conn.execute(
            'SELECT feature FROM region_features WHERE item_id=? AND image_index=? AND box=?',
            (item_id, idx, box_key),
        ).fetchone()
        return np.frombuffer(row[0], dtype=np.float32) if row else None

    def add(self, item_id, image_index, box, feature):
        item_id, idx, box_key = self._key(item_id, image_index, box)
        self.conn.execute(
            'INSERT OR REPLACE INTO region_features VALUES (?, ?, ?, ?)',
            (item_id, idx, box_key, np.asarray(feature, dtype=np.float32).tobytes()),
        )
        self.conn.commit()  # commit per-row: a crash loses at most this one insert

    def count(self):
        return self.conn.execute('SELECT COUNT(*) FROM region_features').fetchone()[0]


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
                             help='SQLite cache of extracted (item_id, character, feature) data. Always '
                                  'used (no opt-in needed) and always resumed from — a previous run that '
                                  "was interrupted partway just continues where it left off. Default: "
                                  "character_features_<backend>.sqlite3 under the tagger's own cache dir; "
                                  'pass a custom path to keep separate caches for parallel experiments.')
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
                             help='SQLite cache of extracted multi-character crop features (raw, before '
                                  'pseudo-labeling). Always used and always resumed from, same as '
                                  '--feature-cache. Default: character_features_multi_<backend>.sqlite3.')
        parser.add_argument('--region-feature-cache', type=str, default=None,
                             help='SQLite cache of extracted manually-labeled-region crop features (see '
                                  'RegionFeatureCache). Always used and always resumed from, same as '
                                  '--feature-cache. Default: character_features_region_<backend>.sqlite3.')

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

        default_cache_name = (
            f'character_features_{backend_choice}.sqlite3' if feature_source == 'tags'
            else f'character_features_{backend_choice}_{feature_source}.sqlite3'
        )
        feature_cache_path = options['feature_cache'] or os.path.join(tagger._data_dir(), default_cache_name)
        # Pre-SQLite-rewrite cache from an earlier training run under the
        # old code (see FeatureCache's own docstring) — only relevant when
        # `feature_cache_path` is still at its default location, since a
        # custom --feature-cache path was never used by the old .joblib
        # naming convention either.
        legacy_joblib_path = None
        if not options['feature_cache']:
            legacy_name = (
                f'character_features_{backend_choice}.joblib' if feature_source == 'tags'
                else f'character_features_{backend_choice}_{feature_source}.joblib'
            )
            legacy_joblib_path = os.path.join(tagger._data_dir(), legacy_name)
        try:
            feature_cache = FeatureCache(feature_cache_path, tagger_backend, feature_source, legacy_joblib_path)
        except _CacheLockedError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return

        already_cached = feature_cache.count()
        if already_cached:
            self.stdout.write(f'Resuming from {feature_cache_path}: {already_cached} image(s) already '
                               'extracted in a previous run.')
        general_tag_names = self._extract_features(
            feature_cache, min_images, tagger_backend, feature_source, options['max_images_per_character'],
        )
        if general_tag_names is None:
            return
        raw_rows = feature_cache.all_rows()  # [(item_id, character, feature_vector), ...]
        self.stdout.write(f'{len(raw_rows)} total (item, character, feature) rows available '
                           f'(cache: {feature_cache_path}).\n')

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
            # Manual region labels (see Item.character_regions, populated via
            # RegionLabelQueueManager.jsx/RegionAnnotator.jsx) are ground
            # truth — a human already said "this box is character X" — so
            # they skip the teacher-classifier confidence gate entirely
            # unlike the automatic bootstrap path below. This is the ONLY
            # way a character that never appears alone (only in CP/MULTIPLE
            # images) gets any multi-character training data at all: the
            # bootstrap teacher has never seen such a character, so its
            # confidence for it is never reliable enough to clear
            # --bootstrap-confidence on its own.
            manual_rows = self._get_manual_labeled_rows(options, tagger_backend, general_tag_names)

            multi_rows = self._get_multi_character_rows(options, tagger_backend, general_tag_names)
            pseudo_rows = self._bootstrap_label(
                multi_rows, teacher, options['bootstrap_confidence'],
            )

            combined_extra = manual_rows + pseudo_rows
            if combined_extra:
                X_boot = np.stack([f for _c, f in combined_extra])
                y_boot = np.array([c for c, _f in combined_extra])
                X_combined = np.concatenate([X_train, X_boot])
                y_combined = np.concatenate([y_train, y_boot])
                self.stdout.write(
                    f'\nAdding {len(manual_rows)} manually-labeled + {len(pseudo_rows)} bootstrap-labeled '
                    f'crops to the {len(X_train)} single-character training examples...'
                )
                teacher_test_acc = final_test_acc
                final_clf, final_train_acc, final_test_acc = fit_and_report(
                    X_combined, y_combined, 'single-character + manual + bootstrap',
                )
                used_bootstrap = True
                self.stdout.write(self.style.SUCCESS(
                    f'\nHoldout accuracy: {teacher_test_acc:.1%} (single-character only) -> '
                    f'{final_test_acc:.1%} (with manual + bootstrap crops)'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    '\nNo manually-labeled regions and no multi-character crops cleared '
                    '--bootstrap-confidence — keeping the single-character-only classifier.'
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

    def _get_manual_labeled_rows(self, options, tagger_backend, expected_general_tag_names):
        """[(character, feature), ...] for every human-labeled region across
        all items with Item.character_regions set (see RegionAnnotator.jsx /
        ItemViewSet.character_regions_view) — already correctly paired, no
        teacher-classifier confidence gating needed (unlike
        _bootstrap_label's output, which this is designed to sit alongside:
        see handle()'s `manual_rows + pseudo_rows` combination).

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

        Backed by a resumable RegionFeatureCache (see its own docstring for
        why it caches only the crop feature, not the resolved label) —
        always used, always resumed from, same as _extract_features. Label
        resolution (the CharacterAliasGroup lookup above) is re-run fresh
        every call regardless of cache state, since it's cheap and can
        change over time independently of the box's pixel content.

        Regions span potentially several of an item's images (see
        Item.character_regions' own docstring) — grouped by image_index per
        item here so each distinct image is only fetched/selected once
        (item.views._select_image_bytes) no matter how many boxes are on
        it, rather than once per region — and only for images that still
        have at least one region not already in the cache.
        """
        from item.views import _select_image_bytes

        backend_choice = options['backend']
        feature_source = options['feature_source']
        default_region_name = (
            f'character_features_region_{backend_choice}.sqlite3' if feature_source == 'tags'
            else f'character_features_region_{backend_choice}_{feature_source}.sqlite3'
        )
        region_cache_path = options['region_feature_cache'] or os.path.join(tagger._data_dir(), default_region_name)
        try:
            region_cache = RegionFeatureCache(region_cache_path, tagger_backend, feature_source)
        except _CacheLockedError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return []

        already_cached = region_cache.count()
        if already_cached:
            self.stdout.write(f'Resuming from {region_cache_path}: {already_cached} region(s) already '
                               'extracted in a previous run.')

        linked_groups = {
            tuple(sorted(set(g.characters))): sorted(set(g.characters))
            for g in CharacterAliasGroup.objects.filter(linked=True)
        }

        items = Item.objects.exclude(character_regions=[]).only('id', 'character_regions')
        rows = []
        skipped_mismatch = 0
        skipped_multi_label = 0
        linked_alias_rows = 0
        newly_extracted = 0
        for item in items.iterator():
            regions = item.character_regions or []
            if not regions:
                continue

            by_image = defaultdict(list)
            for region in regions:
                by_image[region.get('image_index')].append(region)

            for image_index, image_regions in by_image.items():
                # Resolve labels first (cheap, no image access) so the
                # image is only fetched if at least one of its regions
                # actually needs a fresh extraction.
                eligible = []  # (box, label, is_multi_label)
                for region in image_regions:
                    box = region.get('box')
                    names = region.get('characters') or []
                    if not box or not names:
                        continue
                    if len(names) == 1:
                        label = names[0]
                    else:
                        group = linked_groups.get(tuple(sorted(set(names))))
                        if group is None:
                            skipped_multi_label += 1
                            continue
                        label = group[0]  # canonical name — see this method's own docstring
                    eligible.append((box, label, len(names) > 1))

                if not eligible:
                    continue

                needs_image = any(not region_cache.has(item.id, image_index, box) for box, _l, _m in eligible)
                image_bytes = None
                if needs_image:
                    image_bytes, _resolved_index = _select_image_bytes(item, image_index)
                    if image_bytes is None:
                        self.stderr.write(f'item {item.id}: no image available for its manual regions '
                                           f'(image_index={image_index}), skipping')
                        continue

                for box, label, is_multi in eligible:
                    feature = region_cache.get(item.id, image_index, box)
                    if feature is None:
                        try:
                            crop_bytes = tagger._crop_with_padding(image_bytes, tuple(box))
                            feature, feat_names = self._compute_feature(crop_bytes, tagger_backend, feature_source)
                        except Exception as e:
                            self.stderr.write(f'item {item.id}: manual-region feature extraction failed '
                                               f'({e}), skipping region')
                            continue
                        if feat_names != expected_general_tag_names:
                            skipped_mismatch += 1
                            continue
                        region_cache.add(item.id, image_index, box, feature)
                        newly_extracted += 1
                    if is_multi:
                        linked_alias_rows += 1
                    rows.append((label, feature))

        if newly_extracted:
            self.stdout.write(f'{newly_extracted} region(s) newly extracted this run '
                               f'(cache: {region_cache_path}).')
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
        return rows

    def _get_multi_character_rows(self, options, tagger_backend, expected_general_tag_names):
        """Returns [(item_id, candidate_chars, [crop_feature, ...]), ...] for
        multi-character items where person detection found EXACTLY as many
        boxes as the item has confirmed characters — anything else (0/1
        boxes, or a mismatched count) is skipped, since there's no reliable
        way to know which box is which character otherwise. Raw and
        unlabeled — pairing crops to specific character names happens in
        _bootstrap_label, using a teacher classifier that isn't fit yet
        when this runs.

        Backed by a resumable MultiFeatureCache (see its own docstring) —
        always used, always resumed from, same as _extract_features."""
        backend_choice = options['backend']
        feature_source = options['feature_source']
        default_multi_name = (
            f'character_features_multi_{backend_choice}.sqlite3' if feature_source == 'tags'
            else f'character_features_multi_{backend_choice}_{feature_source}.sqlite3'
        )
        multi_cache_path = options['multi_feature_cache'] or os.path.join(tagger._data_dir(), default_multi_name)
        legacy_joblib_path = None
        if not options['multi_feature_cache']:
            legacy_name = (
                f'character_features_multi_{backend_choice}.joblib' if feature_source == 'tags'
                else f'character_features_multi_{backend_choice}_{feature_source}.joblib'
            )
            legacy_joblib_path = os.path.join(tagger._data_dir(), legacy_name)
        try:
            multi_cache = MultiFeatureCache(multi_cache_path, tagger_backend, feature_source, legacy_joblib_path)
        except _CacheLockedError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return []

        already_checked = multi_cache.count_checked()
        if already_checked:
            self.stdout.write(f'Resuming from {multi_cache_path}: {already_checked} multi-character item(s) '
                               'already checked in a previous run.')

        max_chars = options['max_characters_per_item']
        items = Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'id', 'characters', 'preview_data',
        )
        candidates = []  # (item_id, chars, image_bytes)
        for item in items.iterator():
            chars = [c for c in (item.characters or []) if c]
            if not (2 <= len(chars) <= max_chars):
                continue
            if multi_cache.has(item.id):
                continue  # already checked (usable or deterministically not) in a previous run
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                continue
            candidates.append((item.id, chars, image_bytes))

        if not candidates:
            self.stdout.write('\nNo new multi-character items to check (cache up to date).')
            return multi_cache.usable_rows()

        self.stdout.write(f'\n{len(candidates)} new multi-character item(s) to check for a clean '
                           'person-detection match...')
        t0 = time.time()
        newly_usable = 0
        for i, (item_id, chars, image_bytes) in enumerate(candidates):
            try:
                boxes = tagger._detect_person_boxes(image_bytes)
            except Exception as e:
                self.stderr.write(f'item {item_id}: person detection failed ({e}), skipping (will retry later)')
                continue
            if len(boxes) != len(chars):
                multi_cache.add_unusable(item_id)  # a stable fact — never worth re-checking
                continue

            crop_features = []
            ok = True
            for box in boxes:
                try:
                    crop_bytes = tagger._crop_with_padding(image_bytes, box)
                    feature, names = self._compute_feature(crop_bytes, tagger_backend, options['feature_source'])
                except Exception as e:
                    self.stderr.write(f'item {item_id}: crop feature extraction failed ({e}), '
                                       'skipping item (will retry later)')
                    ok = False
                    break
                if names != expected_general_tag_names:
                    self.stderr.write(f'item {item_id}: feature ordering mismatch, skipping item')
                    ok = False
                    break
                crop_features.append(feature)
            if ok and crop_features:
                multi_cache.add(item_id, chars, crop_features)
                newly_usable += 1
            if (i + 1) % 25 == 0 or i + 1 == len(candidates):
                self.stdout.write(f'  checked {i + 1}/{len(candidates)} items, {newly_usable} newly usable '
                                   f'({time.time() - t0:.0f}s elapsed)')

        rows = multi_cache.usable_rows()
        self.stdout.write(self.style.SUCCESS(
            f'\n{multi_cache.count_usable()}/{multi_cache.count_checked()} multi-character items had a clean '
            f'box<->character-count match (cache: {multi_cache_path}).\n'
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

    def _extract_features(self, feature_cache, min_images, tagger_backend, feature_source='tags',
                           max_images_per_character=None):
        """Scans the DB for single-character items and runs the tagger's
        forward pass once per image NOT already in `feature_cache` (see
        FeatureCache — this is what makes an interrupted run resumable,
        and what lets a later run with newly-added items only extract
        those). Returns general_tag_names (None on a hard failure) —
        callers read the actual rows back via feature_cache.all_rows(),
        the cache is the source of truth, not this method's return value.

        Eligibility (>= min_images single-character images per character)
        is still computed fresh every run from the DB directly, deliberately
        NOT filtered by --exclude here, so the same cache stays maximally
        reusable for a later run with a different --exclude list (excluding
        is a cheap post-filter, see handle()).

        `max_images_per_character` caps how many of each character's images
        actually get extracted (first N found, no special sampling) — the
        canary/timm backend runs roughly 6x slower per image than onnx (see
        extract_embedding's docstring), so extracting a character's full
        multi-hundred-image history isn't practical for a quick
        architecture comparison; a capped, smaller-but-still-real sample is
        far more useful than not comparing at all.
        """
        by_char = defaultdict(list)  # character name -> [(item_id, img_order, image_bytes), ...]
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
                by_char[char].extend((item.id, img.order, bytes(img.data)) for img in imgs)
            elif item.preview_data:
                by_char[char].append((item.id, -1, bytes(item.preview_data)))  # -1: legacy single-image field
            if max_images_per_character is not None:
                by_char[char] = by_char[char][:max_images_per_character]

        eligible = {c: imgs for c, imgs in by_char.items() if len(imgs) >= min_images}
        if len(eligible) < 2:
            self.stderr.write(self.style.ERROR(
                f'Only {len(eligible)} character(s) have >= {min_images} single-character images — '
                'need at least 2 distinct classes to train a classifier. Lower --min-images, or gather '
                'more single-character-item data first (see character_image_stats).'
            ))
            return None

        self.stdout.write(f'{len(eligible)} characters qualify (>= {min_images} single-character images each):')
        for c, imgs in sorted(eligible.items(), key=lambda kv: -len(kv[1])):
            self.stdout.write(f'  {len(imgs):>5}  {c}')

        total = sum(len(imgs) for imgs in eligible.values())
        to_extract = [
            (char, item_id, img_order, image_bytes)
            for char, imgs in eligible.items()
            for item_id, img_order, image_bytes in imgs
            if not feature_cache.has(item_id, img_order)
        ]
        already_done = total - len(to_extract)
        general_tag_names = feature_cache.get_general_tag_names()
        if not to_extract:
            self.stdout.write(f'\nAll {total} eligible images already extracted (cache up to date) — '
                               'skipping straight to fit.')
            return general_tag_names

        self.stdout.write(f'\nExtracting features for {len(to_extract)} images '
                           f'({already_done} already cached, {total} total; '
                           f'backend={tagger_backend}, feature_source={feature_source})...')
        t0 = time.time()
        done = 0
        for char, item_id, img_order, image_bytes in to_extract:
            try:
                feature, names = self._compute_feature(image_bytes, tagger_backend, feature_source)
            except Exception as e:
                self.stderr.write(f'item {item_id}#{img_order}: feature extraction failed ({e}), skipping')
                continue
            if general_tag_names is None:
                general_tag_names = names
                feature_cache.set_general_tag_names(names)
            elif names != general_tag_names:
                self.stderr.write(self.style.ERROR(
                    f'item {item_id}#{img_order}: general-tag vocabulary differs from earlier cached rows '
                    f'(the tagger model likely changed since this cache was started) — aborting rather than '
                    f'mixing incompatible feature vectors. Delete the cache to start over.'
                ))
                return None
            feature_cache.add(item_id, img_order, char, feature)
            done += 1
            if done % 50 == 0 or done == len(to_extract):
                self.stdout.write(f'  {done}/{len(to_extract)} ({time.time() - t0:.0f}s elapsed)')

        if feature_cache.count() < 2:
            self.stderr.write(self.style.ERROR('Not enough successfully-extracted features.'))
            return None
        return general_tag_names
