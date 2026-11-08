from django.test import TestCase
from item import tagger
from item.models import Item
from item.views import _aggregate_situation, _collect_candidates, DEFAULT_SITUATION_WEIGHTS, _combine_candidates


class SituationHintTests(TestCase):
    def test_situation_from_tags_yuri(self):
        self.assertEqual(tagger.situation_from_tags(['yuri']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['百合']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'yuri']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['multiple_girls', 'yuri']), 'CP')

    def test_situation_from_tags_2girls_and_kiss(self):
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kiss']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kissing']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'french_kiss']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kiss_on_the_cheek']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kiss_on_the_lips']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'キス']), 'CP')
        self.assertEqual(tagger.situation_from_tags(['2girls', '接吻']), 'CP')

    def test_situation_from_tags_other_kiss(self):
        # 1boy_1girl + kiss -> CP
        self.assertEqual(tagger.situation_from_tags(['1boy_1girl', 'kiss']), 'CP')
        # kissing without solo tag -> CP
        self.assertEqual(tagger.situation_from_tags(['kissing']), 'CP')

    def test_situation_from_tags_regular_multiple(self):
        self.assertEqual(tagger.situation_from_tags(['2girls', 'smile']), 'MULTIPLE')
        self.assertEqual(tagger.situation_from_tags(['3girls', 'smile']), 'MULTIPLE')
        self.assertEqual(tagger.situation_from_tags(['multiple_girls']), 'MULTIPLE')

    def test_situation_from_tags_solo(self):
        self.assertEqual(tagger.situation_from_tags(['1girl', 'solo']), 'SOLO')
        # lipstick_kiss_mark or air_kiss should not falsely trigger CP on solo
        self.assertEqual(tagger.situation_from_tags(['1girl', 'solo', 'lipstick_kiss_mark']), 'SOLO')
        self.assertEqual(tagger.situation_from_tags(['1girl', 'solo', 'air_kiss']), 'SOLO')

    def test_situation_from_tags_r18(self):
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kiss'], rating='explicit'), 'R18')
        self.assertEqual(tagger.situation_from_tags(['2girls', 'kiss', 'r-18']), 'R18')

    def test_aggregate_situation(self):
        self.assertEqual(_aggregate_situation(['CP', 'MULTIPLE']), 'CP')
        self.assertEqual(_aggregate_situation(['MULTIPLE', 'CP']), 'CP')
        self.assertEqual(_aggregate_situation(['SOLO', 'CP']), 'CP')
        self.assertEqual(_aggregate_situation(['CP', 'R18']), 'R18')
        self.assertEqual(_aggregate_situation(['MULTIPLE', 'SOLO']), 'MULTIPLE')
        self.assertEqual(_aggregate_situation(['SOLO', 'SOLO']), 'SOLO')

    def test_collect_candidates_from_item_tags(self):
        item = Item.objects.create(
            external_id=100001,
            situation='',
            tags=['2girls', 'kissing'],
            description='Test item',
        )
        collected = _collect_candidates(item)
        self.assertTrue(any(c['value'] == 'CP' and c['source'] == 'item_tags' for c in collected['situation']))

        values, scores = _combine_candidates(collected['situation'], DEFAULT_SITUATION_WEIGHTS, top_k=1)
        self.assertEqual(values, ['CP'])

    def test_collect_candidates_from_description_hashtags(self):
        item = Item.objects.create(
            external_id=100002,
            situation='',
            tags=[],
            description='Lovely artwork #百合 #イラスト',
        )
        collected = _collect_candidates(item)
        self.assertTrue(any(c['value'] == 'CP' and c['source'] == 'item_tags' for c in collected['situation']))

        values, scores = _combine_candidates(collected['situation'], DEFAULT_SITUATION_WEIGHTS, top_k=1)
        self.assertEqual(values, ['CP'])
