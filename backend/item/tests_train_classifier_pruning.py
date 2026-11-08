import numpy as np
from django.test import TestCase

from item.classifier_training import _build_argv
from item.management.commands.train_character_classifier import Command


class TrainClassifierPruningTests(TestCase):
    def setUp(self):
        self.cmd = Command()

    def test_prune_rows_noop_under_cap(self):
        rows = [{'feat': np.random.randn(10).astype(np.float32), 'kind': 1, 'key': f'k{i}'} for i in range(5)]
        pruned = self.cmd._prune_rows_by_feature(rows, cap=10, strategy='balanced')
        self.assertEqual(len(pruned), 5)

    def test_prune_rows_centroid_drops_outlier(self):
        # Create 10 normal samples near [1, 0, 0] and 2 extreme outliers near [0, 10, 0]
        rows = []
        for i in range(10):
            v = np.array([1.0, np.random.normal(0, 0.05), np.random.normal(0, 0.05)], dtype=np.float32)
            rows.append({'feat': v, 'kind': 1, 'key': f'norm_{i}'})
        for i in range(2):
            v = np.array([0.0, 10.0, 0.0], dtype=np.float32)
            rows.append({'feat': v, 'kind': 1, 'key': f'outlier_{i}'})

        # Cap at 5 using centroid strategy
        pruned = self.cmd._prune_rows_by_feature(rows, cap=5, strategy='centroid')
        self.assertEqual(len(pruned), 5)
        # None of the outliers should be selected
        pruned_keys = [r['key'] for r in pruned]
        self.assertTrue(all(not k.startswith('outlier') for k in pruned_keys))

    def test_prune_rows_diverse_selects_distinct_samples(self):
        # Create clusters in different directions
        rows = []
        # cluster A: [1, 0]
        for i in range(5):
            rows.append({'feat': np.array([1.0, 0.01 * i], dtype=np.float32), 'kind': 1, 'key': f'a_{i}'})
        # cluster B: [0, 1]
        for i in range(5):
            rows.append({'feat': np.array([0.01 * i, 1.0], dtype=np.float32), 'kind': 1, 'key': f'b_{i}'})
        # cluster C: [-1, 0]
        for i in range(5):
            rows.append({'feat': np.array([-1.0, 0.01 * i], dtype=np.float32), 'kind': 1, 'key': f'c_{i}'})

        pruned = self.cmd._prune_rows_by_feature(rows, cap=3, strategy='diverse')
        self.assertEqual(len(pruned), 3)
        # Should pick from different clusters
        prefixes = {r['key'][0] for r in pruned}
        self.assertGreaterEqual(len(prefixes), 2)

    def test_prune_rows_balanced_strategy(self):
        rows = []
        for i in range(20):
            angle = (i / 20.0) * np.pi / 4.0  # cluster within 0 to 45 deg
            v = np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)
            rows.append({'feat': v, 'kind': 1, 'key': f'k_{i}'})
        # Add 3 outliers
        for i in range(3):
            rows.append({'feat': np.array([-1.0, -1.0], dtype=np.float32), 'kind': 1, 'key': f'out_{i}'})

        pruned = self.cmd._prune_rows_by_feature(rows, cap=6, strategy='balanced')
        self.assertEqual(len(pruned), 6)
        pruned_keys = [r['key'] for r in pruned]
        self.assertTrue(all(not k.startswith('out') for k in pruned_keys))

    def test_select_with_prune_strategy(self):
        cands = []
        cached_keys = {f'k_{i}' for i in range(10)}
        for i in range(30):
            cands.append({'char': 'charA', 'kind': 1, 'key': f'k_{i}', 'item_id': i})

        selected, per_char = self.cmd._select(
            cands, exclude=set(), min_images=5, cap=5, cached_keys=cached_keys,
            prune_strategy='balanced'
        )
        # Should keep cached pool (up to 1.5 * cap)
        self.assertGreater(len(selected), 5)
        self.assertLessEqual(len(selected), 15)

    def test_build_argv_includes_prune_strategy(self):
        argv = _build_argv({'backend': 'canary', 'prune_strategy': 'centroid'})
        self.assertIn('--prune-strategy', argv)
        idx = argv.index('--prune-strategy')
        self.assertEqual(argv[idx + 1], 'centroid')
