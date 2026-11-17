from django.test import TestCase
from rest_framework.test import APIClient
from item.models import Item, PreviewImage
from security.token_utils import make_token


class ItemMergeTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.single = Item.objects.create(
            external_id=1001,
            artist='ArtistA',
            titles=['Title1'],
            characters=['CharA'],
            tags=['1girl', 'blue_hair', 'solo'],
            situation='SOLO',
            link='https://example.com/single',
            character_regions=[
                {'image_index': 0, 'box': [10, 10, 100, 100], 'characters': ['CharA']},
            ],
        )
        self.single_img = PreviewImage.objects.create(
            item=self.single, order=0, data=b'IMG_A', content_type='image/png',
        )

        self.bundle = Item.objects.create(
            external_id=1002,
            artist='ArtistA',
            titles=['Title1'],
            characters=['CharA', 'CharB'],
            tags=['solo', 'blue_hair', '1girl'],
            situation='DUO',
            link='https://example.com/bundle',
            character_regions=[
                {'image_index': 0, 'box': [20, 20, 200, 200], 'characters': ['CharB']},
            ],
            checkpoints=[
                {'page_index': 1, 'label': 'Part 2'},
            ],
        )
        self.bundle_img0 = PreviewImage.objects.create(
            item=self.bundle, order=0, data=b'IMG_B', content_type='image/png',
        )
        self.bundle_img1 = PreviewImage.objects.create(
            item=self.bundle, order=1, data=b'IMG_C', content_type='image/png',
        )

        self.other = Item.objects.create(
            external_id=1003,
            artist='ArtistB',
            tags=['1boy', 'black_hair'],
        )

    def test_merge_candidates_groups_identical_tags(self):
        res = self.client.get('/api/items/merge_candidates/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['total_groups'], 1)
        group = data['groups'][0]
        self.assertTrue(group['same_artist'])
        self.assertEqual(group['tag_count'], 3)
        ids = {it['id'] for it in group['items']}
        self.assertEqual(ids, {self.single.id, self.bundle.id})

    def test_merge_append_source_image_and_remap_regions(self):
        # Merge single into bundle, appending single_img at the end (index 2)
        res = self.client.post(
            '/api/items/merge/',
            {
                'target_id': self.bundle.id,
                'source_ids': [self.single.id],
                'images': [
                    {'type': 'preview', 'id': self.bundle_img0.id},
                    {'type': 'preview', 'id': self.bundle_img1.id},
                    {'type': 'preview', 'id': self.single_img.id},
                ],
                'titles': ['Title1'],
                'characters': ['CharA', 'CharB'],
                'tags': ['1girl', 'blue_hair', 'solo'],
                'situation': 'DUO',
                'artist': 'ArtistA',
                'link': 'https://example.com/bundle',
                'description': 'merged desc',
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'merged')
        self.assertEqual(data['deleted_ids'], [self.single.id])

        # Source item deleted
        self.assertFalse(Item.objects.filter(pk=self.single.id).exists())

        # Target item has 3 images in order B, C, A
        self.bundle.refresh_from_db()
        imgs = list(self.bundle.preview_images.order_by('order').values_list('data', flat=True))
        self.assertEqual(imgs, [b'IMG_B', b'IMG_C', b'IMG_A'])

        # Regions remapped: bundle's index 0 stays 0, single's index 0 becomes 2
        regions = sorted(self.bundle.character_regions, key=lambda r: r['image_index'])
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0]['image_index'], 0)
        self.assertEqual(regions[0]['characters'], ['CharB'])
        self.assertEqual(regions[1]['image_index'], 2)
        self.assertEqual(regions[1]['characters'], ['CharA'])

        # Checkpoints preserved at index 1
        self.assertEqual(self.bundle.checkpoints, [{'page_index': 1, 'label': 'Part 2'}])

    def test_merge_drop_duplicate_source_image(self):
        # Merge single into bundle, dropping single_img (only keeping bundle's images)
        res = self.client.post(
            '/api/items/merge/',
            {
                'target_id': self.bundle.id,
                'source_ids': [self.single.id],
                'images': [
                    {'type': 'preview', 'id': self.bundle_img0.id},
                    {'type': 'preview', 'id': self.bundle_img1.id},
                ],
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Item.objects.filter(pk=self.single.id).exists())
        self.bundle.refresh_from_db()
        imgs = list(self.bundle.preview_images.order_by('order').values_list('data', flat=True))
        self.assertEqual(imgs, [b'IMG_B', b'IMG_C'])
        # Region from dropped single_img is also dropped
        self.assertEqual(len(self.bundle.character_regions), 1)
        self.assertEqual(self.bundle.character_regions[0]['image_index'], 0)

    def test_artist_stats(self):
        res = self.client.get('/api/items/artist_stats/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['total_items'], 3)
        self.assertEqual(data['total_artists'], 2)
        self.assertEqual(data['artists'][0]['artist'], 'ArtistA')
        self.assertEqual(data['artists'][0]['item_count'], 2)
        self.assertEqual(data['artists'][0]['image_count'], 3)
        self.assertEqual(data['artists'][1]['artist'], 'ArtistB')
        self.assertEqual(data['artists'][1]['item_count'], 1)

    def test_query_param_filter(self):
        res = self.client.get('/api/items/?q=ArtistA')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data['results'] if isinstance(data, dict) and 'results' in data else data
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['artist'] == 'ArtistA' for r in results))

    def test_title_merge_candidates_and_merge_titles(self):
        from item.models import CharacterGroup, TitleDanbooruLink

        # 3 items sharing both "TitleA" and "Title A" (should be detected at default min_overlap=3)
        t1 = Item.objects.create(external_id=2001, source='pixiv_bookmark', artist='X', titles=['TitleA', 'Title A'])
        t2 = Item.objects.create(external_id=2002, source='pixiv_bookmark', artist='X', titles=['Title A', 'TitleA'])
        t3 = Item.objects.create(external_id=2003, source='pixiv_bookmark', artist='X', titles=['TitleA', 'Title A'])
        # 1 item with only "Title A", 1 item with only "TitleA"
        t4 = Item.objects.create(external_id=2004, source='pixiv_bookmark', artist='X', titles=['Title A'])
        t5 = Item.objects.create(external_id=2005, source='pixiv_bookmark', artist='X', titles=['TitleA'])
        # 2 items sharing "Other1" and "Other2" (below min_overlap=3)
        Item.objects.create(external_id=2006, source='pixiv_bookmark', artist='X', titles=['Other1', 'Other2'])
        Item.objects.create(external_id=2007, source='pixiv_bookmark', artist='X', titles=['Other1', 'Other2'])

        grp = CharacterGroup.objects.create(name='GrpT', characters=['C1'], titles=['Title A', 'Extra'])
        TitleDanbooruLink.objects.create(title_name='Title A', danbooru_tag='title_a')

        res = self.client.get('/api/items/title_merge_candidates/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['total_candidates'], 1)
        cand = data['candidates'][0]
        self.assertEqual(cand['co_count'], 3)
        self.assertEqual({cand['title_a'], cand['title_b']}, {'TitleA', 'Title A'})

        res_merge = self.client.post('/api/items/merge_titles/', {
            'source_title': 'Title A',
            'target_title': 'TitleA',
        }, format='json')
        self.assertEqual(res_merge.status_code, 200)
        self.assertEqual(res_merge.json()['updated_items'], 4)
        self.assertEqual(res_merge.json()['updated_groups'], 1)

        for it in (t1, t2, t3, t4, t5):
            it.refresh_from_db()
            self.assertEqual(it.titles, ['TitleA'])

        grp.refresh_from_db()
        self.assertEqual(grp.titles, ['TitleA', 'Extra'])
        self.assertFalse(TitleDanbooruLink.objects.filter(title_name='Title A').exists())
        self.assertTrue(TitleDanbooruLink.objects.filter(title_name='TitleA', danbooru_tag='title_a').exists())

        # Candidate list is now empty
        res_after = self.client.get('/api/items/title_merge_candidates/')
        self.assertEqual(res_after.status_code, 200)
        self.assertEqual(res_after.json()['total_candidates'], 0)

    def test_character_tiered_color_fallback(self):
        from item.views import (
            _is_appearance_color_label,
            _combine_character_candidates_tiered,
            DEFAULT_ENSEMBLE_WEIGHTS,
        )

        # Color/hair labels are recognized, while proper names with color words are preserved
        self.assertTrue(_is_appearance_color_label('silver'))
        self.assertTrue(_is_appearance_color_label('blonde'))
        self.assertTrue(_is_appearance_color_label('blonde hair'))
        self.assertTrue(_is_appearance_color_label('黒髪'))
        self.assertFalse(_is_appearance_color_label('cure white'))
        self.assertFalse(_is_appearance_color_label('silver wolf'))
        self.assertFalse(_is_appearance_color_label('nagao kagetora (fate)'))

        # Case 1: Named character clears threshold (e.g. classifier > 0.65 -> score > 2.6) -> color labels are filtered out
        entries_with_named = [
            {'value': 'silver', 'source': 'classifier', 'confidence': 0.8},
            {'value': 'blonde', 'source': 'classifier', 'confidence': 0.6},
            {'value': 'nagao kagetora (fate)', 'source': 'classifier', 'confidence': 0.7},
        ]
        vals, _scores = _combine_character_candidates_tiered(entries_with_named, DEFAULT_ENSEMBLE_WEIGHTS, top_k=5)
        self.assertEqual(vals, ['nagao kagetora (fate)'])

        # Case 2: No named character clears threshold -> color labels appear as fallback
        entries_no_named = [
            {'value': 'silver', 'source': 'classifier', 'confidence': 0.7},
            {'value': 'white', 'source': 'classifier', 'confidence': 0.5},
            {'value': 'weak_char', 'source': 'tag_similarity_weak', 'confidence': 0.3},  # 0.3 * 0.5 = 0.15 < 1.0
        ]
        vals_fb, _scores_fb = _combine_character_candidates_tiered(entries_no_named, DEFAULT_ENSEMBLE_WEIGHTS, top_k=5)
        self.assertEqual(vals_fb, ['silver', 'white', 'weak_char'])

        # Case 3: _predict_crop_character_scores filters out named classifier predictions <= 0.85
        from unittest.mock import patch
        from item.views import _predict_crop_character_scores
        with patch('item.tagger.predict_character_topk', return_value=[
            ('low_conf_char', 0.60),
            ('silver', 0.35),
        ]):
            crop_scores = _predict_crop_character_scores({'general_probs': [0.1]}, 'onnx')
        self.assertNotIn('low_conf_char', crop_scores)
        self.assertIn('silver', crop_scores)

    def test_per_image_title_character_cross_boost(self):
        from unittest.mock import patch
        from item.models import CharacterGroup, PreviewImage
        from item.views import _suggest_for_item_ensemble

        CharacterGroup.objects.create(name='GrpA', characters=['CharA'], titles=['TitleA'])
        CharacterGroup.objects.create(name='GrpB', characters=['CharB'], titles=['TitleB'])

        item = Item.objects.create(
            external_id=3001,
            source='pixiv_bookmark',
            artist='MultiArtist',
            situation='MULTIPLE',
        )
        PreviewImage.objects.create(item=item, order=0, data=b'IMG0')
        PreviewImage.objects.create(item=item, order=1, data=b'IMG1')

        # Image 0 detects CharA, Image 1 detects CharB
        fake_results = [
            {
                'characters': [{'name': 'CharA', 'score': 0.92}],
                'tags': [],
                'tags_full': [],
                'situation_hint': None,
                'general_probs': [0.1],
                'embedding': None,
                'ccip': None,
                'crops': [],
                'boxes': [],
                'regions': [],
                'person_count': 1,
            },
            {
                'characters': [{'name': 'CharB', 'score': 0.88}],
                'tags': [],
                'tags_full': [],
                'situation_hint': None,
                'general_probs': [0.1],
                'embedding': None,
                'ccip': None,
                'crops': [],
                'boxes': [],
                'regions': [],
                'person_count': 1,
            },
        ]
        call_idx = {'i': 0}

        def fake_suggest_tags(*_args, **_kwargs):
            res = fake_results[call_idx['i']]
            call_idx['i'] += 1
            return res

        with patch('item.views.HAVE_TAGGER', True), \
             patch('item.tagger.suggest_tags', side_effect=fake_suggest_tags), \
             patch('item.tagger.is_monochrome', return_value=False), \
             patch('item.tagger.predict_character_topk', return_value=[]):
            out = _suggest_for_item_ensemble(item, external=False, max_images=4)

        self.assertEqual(len(out['per_image']), 2)
        # Image 0 boosted TitleA & CharA; Image 1 independently boosted TitleB & CharB
        p0_chars = [c['name'] for c in out['per_image'][0]['characters']]
        p0_titles = [t['name'] for t in out['per_image'][0]['titles']]
        p1_chars = [c['name'] for c in out['per_image'][1]['characters']]
        p1_titles = [t['name'] for t in out['per_image'][1]['titles']]
        self.assertIn('CharA', p0_chars)
        self.assertIn('TitleA', p0_titles)
        self.assertNotIn('TitleB', p0_titles)
        self.assertIn('CharB', p1_chars)
        self.assertIn('TitleB', p1_titles)
        self.assertNotIn('TitleA', p1_titles)

        # Combined item-level candidates retain both titles and characters
        all_titles = [t['name'] for t in out['titles']]
        all_chars = [c['name'] for c in out['characters']]
        self.assertIn('TitleA', all_titles)
        self.assertIn('TitleB', all_titles)
        self.assertIn('CharA', all_chars)
        self.assertIn('CharB', all_chars)
