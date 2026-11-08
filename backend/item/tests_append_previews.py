"""Tests for save_previews append and clear_existing functionality."""

import base64
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
