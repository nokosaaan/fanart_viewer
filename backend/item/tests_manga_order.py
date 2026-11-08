from django.test import TestCase
from rest_framework.test import APIClient
from item.models import Item, PreviewImage
from item.views import _natural_sort_key, _reorder_preview_images
from security.token_utils import make_token


class NaturalSortTestCase(TestCase):
    def test_natural_sort_key(self):
        files = ['21.jpg', '25.jpg', '30.webp', '31.webp', '38.webp', '05.jpg', '06.jpg', '15.webp', '23.jpg', '43.webp']
        sorted_files = sorted(files, key=_natural_sort_key)
        self.assertEqual(
            sorted_files,
            ['05.jpg', '06.jpg', '15.webp', '21.jpg', '23.jpg', '25.jpg', '30.webp', '31.webp', '38.webp', '43.webp']
        )

    def test_natural_sort_key_non_padded(self):
        files = ['10.jpg', '1.jpg', '2.jpg', '20.jpg']
        sorted_files = sorted(files, key=_natural_sort_key)
        self.assertEqual(sorted_files, ['1.jpg', '2.jpg', '10.jpg', '20.jpg'])


class PreviewReorderTestCase(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            external_id=99999,
            source='manual',
            character_regions=[
                {'image_index': 0, 'characters': ['A']},
                {'image_index': 1, 'characters': ['B']},
            ]
        )
        self.p1 = PreviewImage.objects.create(item=self.item, order=0, data=b'img1', filename='21.jpg')
        self.p2 = PreviewImage.objects.create(item=self.item, order=1, data=b'img2', filename='05.jpg')
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_reorder_by_ids(self):
        _reorder_preview_images(self.item, [self.p2.id, self.p1.id])
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(self.p2.order, 0)
        self.assertEqual(self.p1.order, 1)

        # check regions remapped: 0 -> 1, 1 -> 0
        self.assertEqual(self.item.character_regions[0]['image_index'], 1)
        self.assertEqual(self.item.character_regions[1]['image_index'], 0)

    def test_reorder_api_sort_by_filename(self):
        # action=sort_by_filename
        resp = self.client.post(f'/api/items/{self.item.id}/reorder_previews/', {'action': 'sort_by_filename'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        # 05.jpg comes before 21.jpg
        self.assertEqual(self.p2.order, 0)
        self.assertEqual(self.p1.order, 1)
        previews = resp.json()['previews']
        self.assertEqual(previews[0]['id'], self.p2.id)
        self.assertEqual(previews[0]['filename'], '05.jpg')
        self.assertEqual(previews[1]['id'], self.p1.id)
        self.assertEqual(previews[1]['filename'], '21.jpg')

    def test_reorder_api_reverse(self):
        # action=reverse
        resp = self.client.post(f'/api/items/{self.item.id}/reorder_previews/', {'action': 'reverse'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.order, 1)
        self.assertEqual(self.p2.order, 0)


class ProposedRegionsMultipleOccurrencesTestCase(TestCase):
    def test_single_character_assigned_to_all_boxes(self):
        from item.views import _derive_proposed_regions
        # Manga page with 3 panels, item only has 1 character "Alice"
        results = [(0, {
            'crops': [
                {'box': [10, 10, 50, 50], 'characters': []},
                {'box': [60, 60, 100, 100], 'characters': []},
                {'box': [110, 110, 150, 150], 'characters': []},
            ],
            'boxes': [[10, 10, 50, 50], [60, 60, 100, 100], [110, 110, 150, 150]],
        })]
        proposed = _derive_proposed_regions(results, ['Alice'], 'onnx', image_indices=[0])
        self.assertEqual(len(proposed), 3)
        self.assertEqual(proposed[0]['characters'], ['Alice'])
        self.assertEqual(proposed[1]['characters'], ['Alice'])
        self.assertEqual(proposed[2]['characters'], ['Alice'])

    def test_same_character_in_multiple_boxes_multi_char(self):
        from unittest.mock import patch
        from item.views import _derive_proposed_regions
        # 3 boxes, Alice in box 0 and box 2, Bob in box 1
        results = [(0, {
            'crops': [
                {'box': [10, 10, 50, 50]},
                {'box': [60, 60, 100, 100]},
                {'box': [110, 110, 150, 150]},
            ],
            'boxes': [[10, 10, 50, 50], [60, 60, 100, 100], [110, 110, 150, 150]],
        })]
        mock_scores = [
            {'Alice': 0.9, 'Bob': 0.1},
            {'Alice': 0.1, 'Bob': 0.9},
            {'Alice': 0.85, 'Bob': 0.2},
        ]
        with patch('item.views._predict_crop_character_scores', side_effect=mock_scores):
            proposed = _derive_proposed_regions(results, ['Alice', 'Bob'], 'onnx', image_indices=[0])
            self.assertEqual(len(proposed), 3)
            self.assertEqual(proposed[0]['characters'], ['Alice'])
            self.assertEqual(proposed[1]['characters'], ['Bob'])
            self.assertEqual(proposed[2]['characters'], ['Alice'])

    def test_reorder_api_move(self):
        # move p1 (currently index 0) forward
        resp = self.client.post(f'/api/items/{self.item.id}/reorder_previews/', {'action': 'move', 'preview_id': self.p1.id, 'direction': 1}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.order, 1)
        self.assertEqual(self.p2.order, 0)


class ProposedRegionsMultipleOccurrencesTestCase(TestCase):
    def test_single_character_assigned_to_all_boxes(self):
        from item.views import _derive_proposed_regions
        # Manga page with 3 panels, item only has 1 character "Alice"
        results = [(0, {
            'crops': [
                {'box': [10, 10, 50, 50], 'characters': []},
                {'box': [60, 60, 100, 100], 'characters': []},
                {'box': [110, 110, 150, 150], 'characters': []},
            ],
            'boxes': [[10, 10, 50, 50], [60, 60, 100, 100], [110, 110, 150, 150]],
        })]
        proposed = _derive_proposed_regions(results, ['Alice'], 'onnx', image_indices=[0])
        self.assertEqual(len(proposed), 3)
        self.assertEqual(proposed[0]['characters'], ['Alice'])
        self.assertEqual(proposed[1]['characters'], ['Alice'])
        self.assertEqual(proposed[2]['characters'], ['Alice'])

    def test_same_character_in_multiple_boxes_multi_char(self):
        from unittest.mock import patch
        from item.views import _derive_proposed_regions
        # 3 boxes, Alice in box 0 and box 2, Bob in box 1
        results = [(0, {
            'crops': [
                {'box': [10, 10, 50, 50]},
                {'box': [60, 60, 100, 100]},
                {'box': [110, 110, 150, 150]},
            ],
            'boxes': [[10, 10, 50, 50], [60, 60, 100, 100], [110, 110, 150, 150]],
        })]
        mock_scores = [
            {'Alice': 0.9, 'Bob': 0.1},
            {'Alice': 0.1, 'Bob': 0.9},
            {'Alice': 0.85, 'Bob': 0.2},
        ]
        with patch('item.views._predict_crop_character_scores', side_effect=mock_scores):
            proposed = _derive_proposed_regions(results, ['Alice', 'Bob'], 'onnx', image_indices=[0])
            self.assertEqual(len(proposed), 3)
            self.assertEqual(proposed[0]['characters'], ['Alice'])
            self.assertEqual(proposed[1]['characters'], ['Bob'])
            self.assertEqual(proposed[2]['characters'], ['Alice'])


class ManualCreateAndGallerydlFetchTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_create_manual_rejects_all_empty(self):
        resp = self.client.post('/api/items/create_manual/', {}, format='multipart')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Item.objects.count(), 0)

    def test_create_manual_allows_url_only(self):
        resp = self.client.post(
            '/api/items/create_manual/',
            {'link': 'https://example.com/gallery/123/'},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['status'], 'created')
        self.assertEqual(data['item']['link'], 'https://example.com/gallery/123/')
        self.assertFalse(data['item']['has_preview'])

    def test_create_manual_allows_artist_only(self):
        resp = self.client.post(
            '/api/items/create_manual/',
            {'artist': 'some_artist'},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['item']['artist'], 'some_artist')
        self.assertFalse(data['item']['has_preview'])

    def test_fetch_and_save_preview_gallerydl_multi_image(self):
        from unittest.mock import patch
        it = Item.objects.create(
            external_id=10001,
            source='manual',
            link='https://example.com/gallery/123/',
        )
        fake_img_1 = b'\xff\xd8\xff' + (b'A' * 60000)
        fake_img_2 = b'\xff\xd8\xff' + (b'B' * 60000)
        with patch('item.views.validate_url', return_value=None), \
             patch('item.views._fetch_image_via_requests', return_value=(None, None)), \
             patch('item.views.fetch_media_gallerydl', return_value=([(fake_img_1, 'image/jpeg'), (fake_img_2, 'image/jpeg')], 'Gallery Title')):
            resp = self.client.post(
                f'/api/items/{it.id}/fetch_and_save_preview/',
                {'url': it.link, 'preview_only': True, 'force_method': 'gallerydl'},
                format='json',
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['preview_only'])
        self.assertEqual(body['method'], 'gallerydl')
        self.assertEqual(len(body['images']), 2)
        self.assertNotEqual(body['images'][0]['url'], body['images'][1]['url'])
        it.refresh_from_db()
        self.assertEqual(it.description, 'Gallery Title')


class SqliteDirectBackupWriteLockTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_write_requests_blocked_while_backup_locked(self):
        from item import drive_backup
        try:
            drive_backup._set_write_locked(True)
            # Read request succeeds
            get_resp = self.client.get('/api/items/')
            self.assertEqual(get_resp.status_code, 200)
            # Write request is blocked with 409
            post_resp = self.client.post(
                '/api/items/create_manual/',
                {'artist': 'blocked_artist'},
                format='multipart',
            )
            self.assertEqual(post_resp.status_code, 409)
        finally:
            drive_backup._set_write_locked(False)

    def test_create_backup_sqlite_uploads_db_directly_with_lock(self):
        import os
        import sqlite3
        import tempfile
        from unittest.mock import patch
        from item import drive_backup

        fd, tmp_db = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        try:
            conn = sqlite3.connect(tmp_db)
            conn.execute('CREATE TABLE t (x INT)')
            conn.execute('INSERT INTO t VALUES (1)')
            conn.commit()
            conn.close()

            observed_locked = []
            uploaded_paths = []

            def fake_upload(service, file_path, filename, folder_id, mimetype, progress_cb):
                observed_locked.append(drive_backup.is_write_locked())
                uploaded_paths.append((file_path, filename, mimetype))
                # Verify SQLite write lock is held on tmp_db during upload
                other = sqlite3.connect(tmp_db, timeout=0.1)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute('INSERT INTO t VALUES (2)')
                finally:
                    other.close()
                return {'id': '123', 'name': filename}

            with self.settings(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': tmp_db}}), \
                 patch('item.drive_backup.get_drive_service', return_value=object()), \
                 patch('item.drive_backup._get_or_create_backup_folder', return_value='folder1'), \
                 patch('item.drive_backup._upload_with_progress', side_effect=fake_upload):
                res = drive_backup._create_backup_sqlite(lambda phase, pct: None)

            self.assertEqual(observed_locked, [True])
            self.assertFalse(drive_backup.is_write_locked())
            self.assertEqual(len(uploaded_paths), 1)
            self.assertEqual(uploaded_paths[0][0], tmp_db)
            self.assertTrue(uploaded_paths[0][1].endswith('.sqlite3'))
            self.assertEqual(uploaded_paths[0][2], 'application/x-sqlite3')
            self.assertEqual(res['id'], '123')
        finally:
            if os.path.exists(tmp_db):
                os.unlink(tmp_db)




