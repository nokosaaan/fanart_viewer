import time
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from item import image_translator


class ImageTranslatorModelSelectionTest(unittest.TestCase):
    def setUp(self):
        image_translator._EXHAUSTED_MODELS.clear()
        image_translator._CACHED_MODEL = None
        image_translator._MODELS_CACHE = None

    def test_model_ranking_favors_flash_lite(self):
        # gemini-3.5-flash-lite and gemini-3.1-flash-lite (500 RPD) should rank higher than gemini-3-flash (20 RPD)
        rank_35_lite = image_translator._model_rank('gemini-3.5-flash-lite')
        rank_31_lite = image_translator._model_rank('gemini-3.1-flash-lite')
        rank_3_flash = image_translator._model_rank('gemini-3-flash')
        rank_25_flash = image_translator._model_rank('gemini-2.5-flash')

        self.assertLess(rank_35_lite, rank_3_flash)
        self.assertLess(rank_31_lite, rank_3_flash)
        self.assertLess(rank_35_lite, rank_31_lite)
        self.assertLess(rank_3_flash, rank_25_flash)

    @patch('item.image_translator.resolve_available_models')
    def test_pick_best_models_exhaustion_demotion(self, mock_resolve):
        mock_resolve.return_value = [
            'gemini-3.5-flash-lite',
            'gemini-3.1-flash-lite',
            'gemini-3-flash',
        ]
        # Initially 3.5-flash-lite should be first
        models = image_translator.pick_best_models('fake-key')
        self.assertEqual(models[0], 'gemini-3.5-flash-lite')

        # If 3.5-flash-lite is exhausted, 3.1-flash-lite becomes first
        image_translator._EXHAUSTED_MODELS['gemini-3.5-flash-lite'] = time.monotonic() + 3600
        models = image_translator.pick_best_models('fake-key')
        self.assertEqual(models[0], 'gemini-3.1-flash-lite')
        self.assertIn('gemini-3.5-flash-lite', models)
        self.assertEqual(models[-1], 'gemini-3.5-flash-lite')

    @patch('item.image_translator.resolve_available_models')
    @patch('item.image_translator._post')
    def test_fallback_on_429_quota_exhaustion(self, mock_post, mock_resolve):
        mock_resolve.return_value = [
            'gemini-3.5-flash-lite',
            'gemini-3.1-flash-lite',
        ]

        def fake_post(model_name, api_key, body, timeout):
            if model_name == 'gemini-3.5-flash-lite':
                # Simulate 429 quota exceeded error
                fp = MagicMock()
                fp.read.return_value = b'{"error": {"code": 429, "message": "Quota exceeded for Requests per day"}}'
                raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, fp)
            return '{"candidates": [{"content": {"parts": [{"text": "[{\\"box_2d\\": [10, 20, 100, 200], \\"original_text\\": \\"Hello\\", \\"translated_text\\": \\"\\u3053\\u3093\\u306b\\u3061\\u306f\\"}]"}]}}]}'

        mock_post.side_effect = fake_post

        # Create dummy 1x1 png image
        dummy_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        results, meta = image_translator.translate_image_with_gemini(dummy_png, 'fake-key', return_meta=True)

        self.assertEqual(meta['model'], 'gemini-3.1-flash-lite')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['translated_text'], 'こんにちは')
        self.assertIn('gemini-3.5-flash-lite', image_translator._EXHAUSTED_MODELS)

