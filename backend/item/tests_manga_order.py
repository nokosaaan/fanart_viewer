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

    def test_reorder_api_move(self):
        # move p1 (currently index 0) forward
        resp = self.client.post(f'/api/items/{self.item.id}/reorder_previews/', {'action': 'move', 'preview_id': self.p1.id, 'direction': 1}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.order, 1)
        self.assertEqual(self.p2.order, 0)

