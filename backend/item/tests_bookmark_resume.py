"""Bookmark scans: skip what is already fetched and keep going, and on a
part-way failure keep the progress and resume at the page that failed."""
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.test import TestCase

from item import poipiku_bookmarks_fetch as poipiku
from item import twitter_gql_fetch as tw
from item.models import Item, PreviewImage, SocialFetchQueueItem
from item.social_queue import MAX_ATTEMPTS, next_pending, record_fetch_result
from item.social_scan import ScanInterrupted


def _tweet_entry(tweet_id):
    return {'entryId': f'tweet-{tweet_id}', 'content': {'itemContent': {'tweet_results': {'result': {'rest_id': str(tweet_id)}}}}}


def _twitter_page(ids, next_cursor):
    entries = [_tweet_entry(i) for i in ids]
    if next_cursor:
        entries.append({'entryId': f'cursor-bottom-{next_cursor}', 'content': {'value': next_cursor}})
    body = {'data': {'bookmark_timeline_v2': {'timeline': {'instructions': [
        {'type': 'TimelineAddEntries', 'entries': entries}]}}}}
    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = body
    return resp


def _scan_twitter(pages, known, max_pages=5, start_cursor=None):
    """pages: {cursor-or-None: response-or-exception}. Returns (result, requested cursors)."""
    requested = []

    def fake_get(url, params, headers):
        import json
        cursor = json.loads(params['variables']).get('cursor')
        requested.append(cursor)
        r = pages[cursor]
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(tw, '_get_with_ratelimit_backoff', fake_get), \
         patch.object(tw, '_get_creds', return_value=('a', 'b')), \
         patch.object(tw, '_extract_media_urls', return_value=['http://x/img.jpg']), \
         patch.object(tw, '_get_screen_name', return_value='artist'), \
         patch.object(tw, '_extract_full_text', return_value='text'), \
         patch.object(tw.time, 'sleep'):
        try:
            result = tw.fetch_account_bookmarks(known, max_pages=max_pages, start_cursor=start_cursor)
        except ScanInterrupted as e:
            result = e
    return result, requested


class TwitterScanTests(TestCase):
    def test_known_items_are_skipped_not_a_stop(self):
        # 3 is already fetched, but 2 and 1 behind it on the same page are not.
        pages = {None: _twitter_page([4, 3, 2, 1], None)}
        (cands, resume), _ = _scan_twitter(pages, known={3})
        self.assertEqual([c['tweet_id'] for c in cands], ['4', '2', '1'])
        self.assertIsNone(resume)

    def test_page_of_only_fetched_items_ends_the_scan(self):
        pages = {None: _twitter_page([5, 4], 'c1'), 'c1': _twitter_page([3, 2], 'c2'), 'c2': _twitter_page([1], None)}
        (cands, resume), requested = _scan_twitter(pages, known={3, 2})
        self.assertEqual([c['tweet_id'] for c in cands], ['5', '4'])
        self.assertIsNone(resume)
        self.assertEqual(requested, [None, 'c1'])  # c2 never requested

    def test_budget_exhausted_returns_cursor(self):
        pages = {None: _twitter_page([5], 'c1'), 'c1': _twitter_page([4], 'c2')}
        (cands, resume), _ = _scan_twitter(pages, known=set(), max_pages=2)
        self.assertEqual(resume, 'c2')

    def test_network_error_keeps_progress_and_failed_page_cursor(self):
        pages = {None: _twitter_page([5, 4], 'c1'), 'c1': _twitter_page([3], 'c2'),
                 'c2': requests.ConnectionError('down')}
        err, requested = _scan_twitter(pages, known=set())
        self.assertIsInstance(err, ScanInterrupted)
        self.assertEqual([c['tweet_id'] for c in err.candidates], ['5', '4', '3'])
        self.assertEqual(err.resume, 'c2')  # the page that failed, not the page before it
        self.assertIsInstance(err.cause, tw.TwitterGQLError)

    def test_resume_then_continues_from_failed_page(self):
        pages = {'c2': _twitter_page([2, 1], None)}
        (cands, resume), requested = _scan_twitter(pages, known=set(), start_cursor='c2')
        self.assertEqual(requested, ['c2'])
        self.assertEqual([c['tweet_id'] for c in cands], ['2', '1'])

    def test_first_page_failure_without_progress_raises_original(self):
        pages = {None: requests.ConnectionError('down')}
        with self.assertRaises(tw.TwitterGQLError):
            with patch.object(tw, '_get_with_ratelimit_backoff', side_effect=requests.ConnectionError('down')), \
                 patch.object(tw, '_get_creds', return_value=('a', 'b')):
                tw.fetch_account_bookmarks(set(), max_pages=2)


def _poipiku_html(illust_ids, next_pg):
    thumbs = ''.join(
        f'<div class="IllustThumb"><a class="IllustThumbImg" href="/1/{i}.html"></a>'
        f'<a class="IllustUser"><h2 class="IllustUserName">u</h2></a></div>' for i in illust_ids)
    nav = (f'<nav class="PageBar"><a class="PageBarItem" href="?PG={next_pg}"></a></nav>' if next_pg else '')
    return f'<html><body>{thumbs}{nav}</body></html>'


class PoipikuScanTests(TestCase):
    def _run(self, responses, known, start_url=None, max_pages=5):
        urls = []

        def fake_get(url, timeout):
            urls.append(url)
            r = responses[len(urls) - 1]
            if isinstance(r, Exception):
                raise r
            return MagicMock(status_code=200, text=r)

        session = MagicMock()
        session.get.side_effect = fake_get
        with patch.object(poipiku, '_session', return_value=session):
            try:
                return poipiku.fetch_account_bookmarks(known, max_pages=max_pages, start_url=start_url), urls
            except ScanInterrupted as e:
                return e, urls

    def test_skip_known_and_keep_going(self):
        (cands, resume), _ = self._run([_poipiku_html([30, 20, 10], None)], known={20})
        self.assertEqual([c['illust_id'] for c in cands], [30, 10])
        self.assertEqual(resume, '')

    def test_network_error_resumes_at_failed_page(self):
        err, urls = self._run([_poipiku_html([3, 2], 2), requests.ConnectionError('down')], known=set())
        self.assertIsInstance(err, ScanInterrupted)
        self.assertEqual([c['illust_id'] for c in err.candidates], [3, 2])
        self.assertEqual(err.resume, urls[1])


class QueueRetryTests(TestCase):
    def _row(self, n, **kw):
        return SocialFetchQueueItem.objects.create(
            platform='pixiv', kind='bookmark', external_id=n, url=f'https://www.pixiv.net/artworks/{n}', **kw)

    def test_failure_keeps_row_pending_until_max_attempts(self):
        row = self._row(1)
        for _ in range(MAX_ATTEMPTS - 1):
            self.assertEqual(record_fetch_result(row, False), 'pending')
        self.assertEqual(record_fetch_result(row, False), 'failed')

    def test_failed_rows_go_to_the_back_of_the_queue(self):
        a, b = self._row(1), self._row(2)
        record_fetch_result(a, False)
        self.assertEqual(next_pending('pixiv').pk, b.pk)
        record_fetch_result(b, True)
        self.assertEqual(next_pending('pixiv').pk, a.pk)

    def test_retry_command_revives_failed_and_skips_fetched(self):
        failed = self._row(1, status='failed', attempts=MAX_ATTEMPTS)
        done_elsewhere = self._row(2, status='failed', attempts=MAX_ATTEMPTS)
        item = Item.objects.create(external_id=2, source='pixiv_bookmark', artist='a',
                                   link='https://www.pixiv.net/artworks/2')
        PreviewImage.objects.create(item=item, order=0, data=b'x')

        call_command('retry_failed_fetches', stdout=StringIO())  # dry run
        failed.refresh_from_db()
        self.assertEqual(failed.status, 'failed')

        call_command('retry_failed_fetches', '--apply', stdout=StringIO())
        failed.refresh_from_db()
        done_elsewhere.refresh_from_db()
        self.assertEqual((failed.status, failed.attempts), ('pending', 0))
        self.assertEqual(done_elsewhere.status, 'skipped')


class DrainRetryTests(TestCase):
    def test_failed_fetch_is_retried_without_creating_a_second_item(self):
        from item.management.commands.poll_pixiv_bookmarks import Command
        url = 'https://www.pixiv.net/artworks/77'
        row = SocialFetchQueueItem.objects.create(
            platform='pixiv', kind='bookmark', external_id=77, url=url, screen_name='u')
        cmd = Command()
        with patch.object(Command, '_fetch_item', return_value=False):
            cmd._drain(5)  # network down: stops after the failure, row stays queued
            cmd._drain(5)
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), ('pending', 2))
        self.assertEqual(Item.objects.filter(link=url).count(), 1)

        def save_preview(item, _url):
            PreviewImage.objects.create(item=item, order=0, data=b'x')
            return True
        with patch.object(Command, '_fetch_item', side_effect=save_preview):
            cmd._drain(5)  # network back: picks up exactly where it failed
        row.refresh_from_db()
        self.assertEqual(row.status, 'done')
        self.assertEqual(Item.objects.filter(link=url).count(), 1)
