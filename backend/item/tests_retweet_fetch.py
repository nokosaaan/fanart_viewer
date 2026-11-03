import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from rest_framework.test import APIClient

from item.models import Item, PreviewImage, SocialFetchQueueItem
from item.views import _known_twitter_ids, _item_has_preview
from item.twitter_gql_fetch import fetch_account_retweets
from security.token_utils import make_token


class KnownTwitterIdsTestCase(TestCase):
    def test_known_twitter_ids_filters_preview(self):
        # Item with PreviewImage
        item_with_preview = Item.objects.create(
            external_id=1001,
            source='twitter_rt',
            artist='artist1',
            link='https://x.com/artist1/status/1001',
        )
        PreviewImage.objects.create(item=item_with_preview, order=0, data=b'fake_data')

        # Item without PreviewImage (e.g. failed/incomplete fetch)
        item_no_preview = Item.objects.create(
            external_id=1002,
            source='twitter_rt',
            artist='artist2',
            link='https://x.com/artist2/status/1002',
        )

        # Queue item pending vs failed
        SocialFetchQueueItem.objects.create(
            platform='twitter',
            kind='bookmark',
            external_id=2001,
            status='pending',
            url='https://x.com/i/status/2001',
        )
        SocialFetchQueueItem.objects.create(
            platform='twitter',
            kind='bookmark',
            external_id=2002,
            status='failed',
            url='https://x.com/i/status/2002',
        )

        known = _known_twitter_ids()
        # 1001 should be known (has preview)
        self.assertIn(1001, known)
        self.assertIn('1001', known)

        # 1002 should NOT be known (has no preview!)
        self.assertNotIn(1002, known)
        self.assertNotIn('1002', known)

        # 2001 should be known (pending queue)
        self.assertIn(2001, known)
        # 2002 should NOT be known (failed queue)
        self.assertNotIn(2002, known)


class FetchAccountRetweetsTestCase(TestCase):
    def _make_tweet_entry(self, tweet_id, screen_name, original_id, media_urls=None):
        # Minimal Twitter GraphQL timeline entry structure
        return {
            "entryId": f"tweet-{tweet_id}",
            "content": {
                "itemContent": {
                    "tweet_results": {
                        "result": {
                            "__typename": "Tweet",
                            "rest_id": str(tweet_id),
                            "core": {
                                "user_results": {
                                    "result": {
                                        "legacy": {"screen_name": "scanner_user"}
                                    }
                                }
                            },
                            "legacy": {
                                "full_text": f"RT @{screen_name}: some art",
                                "retweeted_status_result": {
                                    "result": {
                                        "__typename": "Tweet",
                                        "rest_id": str(original_id),
                                        "core": {
                                            "user_results": {
                                                "result": {
                                                    "legacy": {"screen_name": screen_name}
                                                }
                                            }
                                        },
                                        "legacy": {
                                            "full_text": "Original tweet text",
                                            "extended_entities": {
                                                "media": [
                                                    {"media_url_https": url, "type": "photo"}
                                                    for url in (media_urls or ["https://pbs.twimg.com/media/pic.jpg"])
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

    @patch('item.twitter_gql_fetch._get_creds', return_value=('token', 'ct0'))
    @patch('item.twitter_gql_fetch._resolve_user_id', return_value='12345')
    @patch('item.twitter_gql_fetch._get_with_ratelimit_backoff')
    def test_fetch_account_retweets_skips_known_and_continues(self, mock_get, mock_resolve, mock_creds):
        # Page 1: contains tweet 101 (known) and tweet 102 (known)
        # Page 2: contains tweet 103 (unregistered, new)
        page1_data = {
            "data": {
                "user": {
                    "result": {
                        "timeline": {
                            "timeline": {
                                "instructions": [
                                    {
                                        "type": "TimelineAddEntries",
                                        "entries": [
                                            self._make_tweet_entry("t1", "artist1", 101),
                                            self._make_tweet_entry("t2", "artist2", 102),
                                            {"entryId": "cursor-bottom-1", "content": {"value": "cursor_p2"}}
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

        page2_data = {
            "data": {
                "user": {
                    "result": {
                        "timeline": {
                            "timeline": {
                                "instructions": [
                                    {
                                        "type": "TimelineAddEntries",
                                        "entries": [
                                            self._make_tweet_entry("t3", "artist3", 103),
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

        resp1 = MagicMock()
        resp1.ok = True
        resp1.status_code = 200
        resp1.json.return_value = page1_data

        resp2 = MagicMock()
        resp2.ok = True
        resp2.status_code = 200
        resp2.json.return_value = page2_data

        mock_get.side_effect = [resp1, resp2]

        known_ids = {101, 102, '101', '102'}
        result = fetch_account_retweets("scanner_user", max_items=1, known_ids=known_ids, max_pages=5)

        # It should skip 101 and 102, continue to page 2, and fetch 103!
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(result["already_archived"], 2)
        self.assertEqual(len(result["retweets"]), 1)
        self.assertEqual(result["retweets"][0]["tweet_id"], "103")
        self.assertEqual(result["retweets"][0]["screen_name"], "artist3")


class ScanAccountRetweetsViewTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    @patch('item.views._have_twitter_creds', return_value=True)
    @patch('item.views.fetch_account_retweets')
    def test_scan_account_retweets_reuses_previewless_item(self, mock_fetch, mock_creds):
        # Pre-existing item without preview (from previous failed scan)
        empty_item = Item.objects.create(
            external_id=555,
            source='twitter_rt',
            artist='some_artist',
            link='https://x.com/some_artist/status/555',
        )

        mock_fetch.return_value = {
            "screen_name": "testuser",
            "pages_fetched": 1,
            "already_archived": 0,
            "retweets": [
                {
                    "tweet_id": "555",
                    "screen_name": "some_artist",
                    "media_urls": ["https://pbs.twimg.com/media/test.jpg"],
                    "description": "Art tweet",
                }
            ]
        }

        resp = self.client.post('/api/items/scan_account_retweets/', {'screen_name': 'testuser'}, format='json')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # The previewless item should NOT be skipped as already_archived;
        # it should be reused and returned in items!
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['id'], empty_item.id)
        self.assertEqual(Item.objects.filter(external_id=555).count(), 1)

