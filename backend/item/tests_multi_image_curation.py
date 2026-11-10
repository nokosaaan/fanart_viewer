import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient
from security.token_utils import make_token


class MultiImageCurationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_multi_image_curation_flow(self):
        user_dir = Path(self.tmp_dir.name)
        with patch('pathlib.Path.home', return_value=user_dir):
            # 1. Initially empty
            res = self.client.get('/api/items/multi_image_curation/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {'dismissed_ids': []})

            # 2. Add via dismiss action
            res = self.client.post(
                '/api/items/multi_image_curation/',
                data={'action': 'dismiss', 'id': 123},
                format='json'
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json().get('dismissed_ids'), [123])

            # 3. Add bulk IDs
            res = self.client.post(
                '/api/items/multi_image_curation/',
                data={'dismissed_ids': [456, 789]},
                format='json'
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json().get('dismissed_ids'), [123, 456, 789])

            # 4. Restore (remove) an ID
            res = self.client.post(
                '/api/items/multi_image_curation/',
                data={'action': 'restore', 'id': 456},
                format='json'
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json().get('dismissed_ids'), [123, 789])

            # 5. GET confirms persisted state
            res = self.client.get('/api/items/multi_image_curation/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {'dismissed_ids': [123, 789]})
