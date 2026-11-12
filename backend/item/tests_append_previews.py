"""Tests for save_previews append and clear_existing functionality."""

import base64
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient
from item.models import Item, PreviewImage
from security.token_utils import make_token


class SavePreviewsAppendTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(external_id=999901, link="https://example.com/item")
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _make_dummy_data_uri(self, content: bytes, mime: str = "image/jpeg") -> str:
        b64 = base64.b64encode(content).decode('ascii')
        return f"data:{mime};base64,{b64}"

    def test_initial_save_previews(self):
        # 1. Initial batch: 2 images
        img1 = {'data_uri': self._make_dummy_data_uri(b'img1'), 'url': 'http://ex.com/1.jpg'}
        img2 = {'data_uri': self._make_dummy_data_uri(b'img2'), 'url': 'http://ex.com/2.jpg'}

        resp = self.client.post(
            f'/api/items/{self.item.id}/save_previews/',
            {'images': [img1, img2], 'clear_existing': True},
            format='json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(PreviewImage.objects.filter(item=self.item).count(), 2)

        p0 = PreviewImage.objects.get(item=self.item, order=0)
        p1 = PreviewImage.objects.get(item=self.item, order=1)
        self.assertEqual(bytes(p0.data), b'img1')
        self.assertEqual(bytes(p1.data), b'img2')

    def test_append_mode_preserves_existing_images(self):
        # 1. Initial 2 images
        PreviewImage.objects.create(item=self.item, order=0, data=b'first', content_type='image/jpeg')
        PreviewImage.objects.create(item=self.item, order=1, data=b'second', content_type='image/jpeg')

        # 2. Append 2 more images with append=True
        img3 = {'data_uri': self._make_dummy_data_uri(b'third'), 'url': 'http://ex.com/3.jpg'}
        img4 = {'data_uri': self._make_dummy_data_uri(b'fourth'), 'url': 'http://ex.com/4.jpg'}

        resp = self.client.post(
            f'/api/items/{self.item.id}/save_previews/',
            {'images': [img3, img4], 'append': True, 'start_index': 0},
            format='json'
        )
        self.assertEqual(resp.status_code, 200)

        # Count should now be 4
        all_imgs = list(PreviewImage.objects.filter(item=self.item).order_by('order'))
        self.assertEqual(len(all_imgs), 4)
        self.assertEqual([img.order for img in all_imgs], [0, 1, 2, 3])
        self.assertEqual(bytes(all_imgs[0].data), b'first')
        self.assertEqual(bytes(all_imgs[1].data), b'second')
        self.assertEqual(bytes(all_imgs[2].data), b'third')
        self.assertEqual(bytes(all_imgs[3].data), b'fourth')

    def test_overwrite_mode_clears_existing(self):
        # 1. Initial 2 images
        PreviewImage.objects.create(item=self.item, order=0, data=b'first', content_type='image/jpeg')
        PreviewImage.objects.create(item=self.item, order=1, data=b'second', content_type='image/jpeg')

        # 2. Overwrite with 1 new image (clear_existing=True, append=False)
        new_img = {'data_uri': self._make_dummy_data_uri(b'replacement'), 'url': 'http://ex.com/new.jpg'}

        resp = self.client.post(
            f'/api/items/{self.item.id}/save_previews/',
            {'images': [new_img], 'clear_existing': True, 'append': False},
            format='json'
        )
        self.assertEqual(resp.status_code, 200)

        all_imgs = list(PreviewImage.objects.filter(item=self.item).order_by('order'))
        self.assertEqual(len(all_imgs), 1)
        self.assertEqual(all_imgs[0].order, 0)
        self.assertEqual(bytes(all_imgs[0].data), b'replacement')

    def test_preview_get_by_id(self):
        p = PreviewImage.objects.create(item=self.item, order=0, data=b'stable_bytes', content_type='image/png')
        resp = self.client.get(f'/api/items/{self.item.id}/preview/?id={p.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'stable_bytes')
        self.assertEqual(resp['Content-Type'], 'image/png')

        resp_bad = self.client.get(f'/api/items/{self.item.id}/preview/?id=999999')
        self.assertEqual(resp_bad.status_code, 404)

    def test_fetch_then_upload_then_delete_original_consistency(self):
        # 1. Simulate fetched original image
        orig_img = {'data_uri': self._make_dummy_data_uri(b'original_fetched'), 'url': 'http://ex.com/orig.jpg'}
        r_save = self.client.post(
            f'/api/items/{self.item.id}/save_previews/',
            {'images': [orig_img], 'clear_existing': True},
            format='json'
        )
        self.assertEqual(r_save.status_code, 200)
        orig_pid = PreviewImage.objects.get(item=self.item, order=0).id

        # 2. Manually upload 2 new images via upload_preview
        up1 = SimpleUploadedFile("page1.png", b"uploaded_one", content_type="image/png")
        up2 = SimpleUploadedFile("page2.png", b"uploaded_two", content_type="image/png")
        r_up = self.client.post(
            f'/api/items/{self.item.id}/upload_preview/',
            {'images': [up1, up2]},
            format='multipart'
        )
        self.assertEqual(r_up.status_code, 200)
        up_data = r_up.json()
        self.assertEqual(up_data['added'], 2)
        self.assertEqual(up_data['item']['preview_count'], 3)
        self.assertTrue(up_data['item']['has_preview'])
        self.assertEqual(up_data['item']['first_preview_id'], orig_pid)

        # Attach regions, checkpoints, and translation_caches across indices 0, 1, 2
        self.item.refresh_from_db()
        self.item.character_regions = [
            {'image_index': 0, 'box': [0.1, 0.1, 0.5, 0.5], 'characters': ['CharOrig']},
            {'image_index': 2, 'box': [0.2, 0.2, 0.6, 0.6], 'characters': ['CharUploaded2']},
        ]
        self.item.checkpoints = [
            {'page_index': 1, 'label': 'StartUploaded'},
            {'page_index': 2, 'label': 'End'},
        ]
        self.item.save()
        self.item.translation_caches.create(image_index=0, translations=[{'text': 'old'}])
        self.item.translation_caches.create(image_index=2, translations=[{'text': 'new'}])

        # 3. Delete the original fetched image (orig_pid)
        r_del = self.client.post(
            f'/api/items/{self.item.id}/delete_previews/',
            {'delete_ids': [orig_pid]},
            format='json'
        )
        self.assertEqual(r_del.status_code, 200)
        del_data = r_del.json()
        self.assertEqual(del_data['deleted'], 1)
        self.assertEqual(del_data['remaining'], 2)

        # 4. Verify /previews/ list is contiguous (0, 1) and contains both uploaded images
        r_list = self.client.get(f'/api/items/{self.item.id}/previews/')
        self.assertEqual(r_list.status_code, 200)
        self.assertIn('no-store', r_list['Cache-Control'])
        previews_list = r_list.json()
        self.assertEqual(len(previews_list), 2)
        self.assertEqual([p['index'] for p in previews_list], [0, 1])

        # 5. Verify positional /preview/ and /preview/?index=0,1 and ID-based /preview/?id=...
        r_p0 = self.client.get(f'/api/items/{self.item.id}/preview/')
        self.assertEqual(r_p0.status_code, 200)
        self.assertEqual(r_p0.content, b'uploaded_one')
        self.assertIn('no-store', r_p0['Cache-Control'])

        r_p1 = self.client.get(f'/api/items/{self.item.id}/preview/?index=1')
        self.assertEqual(r_p1.status_code, 200)
        self.assertEqual(r_p1.content, b'uploaded_two')
        self.assertIn('no-store', r_p1['Cache-Control'])

        r_by_id = self.client.get(f"/api/items/{self.item.id}/preview/?id={previews_list[1]['id']}")
        self.assertEqual(r_by_id.status_code, 200)
        self.assertEqual(r_by_id.content, b'uploaded_two')
        self.assertIn('max-age=300', r_by_id['Cache-Control'])

        # 6. Verify GET /api/items/{id}/ returns fresh preview_count, first_preview_id, and remapped metadata
        r_item = self.client.get(f'/api/items/{self.item.id}/')
        self.assertEqual(r_item.status_code, 200)
        item_json = r_item.json()
        self.assertEqual(item_json['preview_count'], 2)
        self.assertEqual(item_json['first_preview_id'], previews_list[0]['id'])
        self.assertEqual(
            item_json['character_regions'],
            [{'image_index': 1, 'box': [0.2, 0.2, 0.6, 0.6], 'characters': ['CharUploaded2']}],
        )
        self.assertEqual(
            item_json['checkpoints'],
            [{'page_index': 0, 'label': 'StartUploaded'}, {'page_index': 1, 'label': 'End'}],
        )
        rem_caches = list(self.item.translation_caches.order_by('image_index').values('image_index', 'translations'))
        self.assertEqual(rem_caches, [{'image_index': 1, 'translations': [{'text': 'new'}]}])

    def test_upload_preview_normalizes_gapped_order(self):
        # Simulate pre-existing rows with gapped order (e.g. order=0 and order=5)
        p_a = PreviewImage.objects.create(item=self.item, order=0, data=b'img_a', content_type='image/jpeg')
        p_b = PreviewImage.objects.create(item=self.item, order=5, data=b'img_b', content_type='image/jpeg')

        up = SimpleUploadedFile("page3.png", b"img_c", content_type="image/png")
        r_up = self.client.post(
            f'/api/items/{self.item.id}/upload_preview/',
            {'images': [up]},
            format='multipart'
        )
        self.assertEqual(r_up.status_code, 200)

        rows = list(PreviewImage.objects.filter(item=self.item).order_by('order', 'id'))
        self.assertEqual([r.order for r in rows], [0, 1, 2])
        self.assertEqual([bytes(r.data) for r in rows], [b'img_a', b'img_b', b'img_c'])

        # Deleting the first image now cleanly shifts img_b -> 0 and img_c -> 1
        r_del = self.client.delete(f'/api/items/{self.item.id}/previews/id/{p_a.id}/')
        self.assertEqual(r_del.status_code, 200)

        rows_after = list(PreviewImage.objects.filter(item=self.item).order_by('order', 'id'))
        self.assertEqual([r.order for r in rows_after], [0, 1])
        self.assertEqual([bytes(r.data) for r in rows_after], [b'img_b', b'img_c'])
        self.assertEqual(rows_after[0].id, p_b.id)
