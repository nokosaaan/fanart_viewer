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
time. Use --exclude for a character that technically has enough images but
isn't actually a single visually-coherent class — e.g. a catch-all bucket
like "any OC with white hair" or "any image featuring the full cast of
title X" isn't one character, and training on it just teaches the
classifier to detect the confound (hair color, or "multiple people
present") instead of an identity.

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

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier --min-images 20 --backend canary

  # First run: extract + cache, excluding known-bad labels
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --exclude white,牢屋敷メンバー --feature-cache /app/data/tagger/char_features_onnx.joblib

  # Later: tweak and refit WITHOUT re-extracting
  docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \\
      --use-cache /app/data/tagger/char_features_onnx.joblib --exclude white,牢屋敷メンバー,ユキ
"""
import os
import time
from collections import defaultdict

import numpy as np
from django.core.management.base import BaseCommand

from item.models import Item
from item import tagger


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
        parser.add_argument('--exclude', type=str, default='',
                             help='Comma-separated character names to drop entirely, even if they clear '
                                  '--min-images — for labels that are not one visually-coherent character '
                                  '(a catch-all OC bucket, a "whole cast" group tag, etc.)')
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

    def handle(self, *args, **options):
        try:
            import joblib
            from sklearn.linear_model import LogisticRegression
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import classification_report
        except ImportError as e:
            self.stderr.write(self.style.ERROR(f'scikit-learn/joblib not available: {e}'))
            return

        min_images = options['min_images']
        exclude = {c.strip() for c in options['exclude'].split(',') if c.strip()}
        backend_choice = options['backend']
        tagger_backend = 'timm' if backend_choice == 'canary' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            self.stderr.write(self.style.ERROR(
                "--backend canary requires the 'timm' backend, which isn't installed on this server "
                '(see requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg).'
            ))
            return

        general_tag_names = None
        if options['use_cache']:
            self.stdout.write(f"Loading cached features from {options['use_cache']}...")
            cache = joblib.load(options['use_cache'])
            if cache.get('backend') != tagger_backend:
                self.stderr.write(self.style.ERROR(
                    f"Cache was extracted with backend={cache.get('backend')!r}, but --backend={backend_choice!r} "
                    f"(tagger_backend={tagger_backend!r}) was requested — features aren't compatible across backends."
                ))
                return
            raw_rows = cache['rows']  # [(item_id, character, feature_vector), ...]
            general_tag_names = cache['general_tag_names']
            self.stdout.write(f'Loaded {len(raw_rows)} cached (item, character, feature) rows.\n')
        else:
            raw_rows, general_tag_names = self._extract_features(min_images, tagger_backend)
            if raw_rows is None:
                return
            feature_cache_path = options['feature_cache'] or os.path.join(
                tagger._data_dir(), f'character_features_{backend_choice}.joblib',
            )
            joblib.dump({'rows': raw_rows, 'backend': tagger_backend, 'general_tag_names': general_tag_names},
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

        # Train/holdout split, fit, report — class_weight='balanced' so the
        # smallest included classes aren't drowned out by the largest ones.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=options['test_size'], random_state=options['random_state'], stratify=y,
        )
        clf = LogisticRegression(max_iter=2000, class_weight='balanced')
        clf.fit(X_train, y_train)

        train_acc = clf.score(X_train, y_train)
        test_acc = clf.score(X_test, y_test)
        self.stdout.write(f'\nTrain accuracy: {train_acc:.1%}  |  Holdout accuracy: {test_acc:.1%}\n')
        self.stdout.write('Per-class holdout report:')
        self.stdout.write(classification_report(y_test, clf.predict(X_test), zero_division=0))

        # Save — self-contained: records which backend/tag ordering produced
        # these features, so a later inference-side integration doesn't have
        # to assume anything matches.
        output_path = options['output'] or os.path.join(
            tagger._data_dir(), f'character_classifier_{backend_choice}.joblib',
        )
        joblib.dump({
            'classifier': clf,
            'classes': list(clf.classes_),
            'backend': tagger_backend,
            'general_tag_names': general_tag_names,
            'min_images': min_images,
            'excluded': sorted(exclude),
            'train_accuracy': train_acc,
            'holdout_accuracy': test_acc,
        }, output_path)
        self.stdout.write(self.style.SUCCESS(f'\nSaved classifier to {output_path}'))

    def _extract_features(self, min_images, tagger_backend):
        """Scans the DB for single-character items and runs the tagger's
        forward pass once per image. Returns (rows, general_tag_names) where
        rows is [(item_id, character, feature_vector), ...] for every
        character with >= min_images single-character images — deliberately
        NOT filtered by --exclude here, so the resulting feature-cache file
        stays maximally reusable for a later run with a different --exclude
        list (excluding is a cheap post-filter, see handle())."""
        by_char = defaultdict(list)  # character name -> [(item_id, image_bytes), ...]
        items = Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'id', 'characters', 'preview_data',
        )
        for item in items.iterator():
            chars = [c for c in (item.characters or []) if c]
            if len(chars) != 1:
                continue  # v1 scope: single-character items only
            char = chars[0]
            imgs = list(item.preview_images.all())
            if imgs:
                by_char[char].extend((item.id, bytes(img.data)) for img in imgs)
            elif item.preview_data:
                by_char[char].append((item.id, bytes(item.preview_data)))

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
        self.stdout.write(f'\nExtracting features for {total} images (backend={tagger_backend})...')
        rows = []
        general_tag_names = None
        t0 = time.time()
        done = 0
        for char, imgs in eligible.items():
            for item_id, image_bytes in imgs:
                try:
                    preds, tag_names, _rating_idx, general_idx, _character_idx = tagger._raw_predict(
                        image_bytes, None, tagger_backend,
                    )
                except Exception as e:
                    self.stderr.write(f'item {item_id}: feature extraction failed ({e}), skipping')
                    continue
                if general_tag_names is None:
                    general_tag_names = [tag_names[i] for i in general_idx]
                feature = np.asarray(preds, dtype=np.float32)[general_idx]
                rows.append((item_id, char, feature))
                done += 1
                if done % 50 == 0 or done == total:
                    self.stdout.write(f'  {done}/{total} ({time.time() - t0:.0f}s elapsed)')

        if len(rows) < 2:
            self.stderr.write(self.style.ERROR('Not enough successfully-extracted features.'))
            return None, None
        return rows, general_tag_names
