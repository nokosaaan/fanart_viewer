import os
from django.core.management.base import BaseCommand

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:
    HAVE_PLAYWRIGHT = False

import base64
import threading

from item.models import Item, PreviewImage
from item.playwright_helper import fetch_images_with_playwright


class Command(BaseCommand):
    help = 'Login to Pixiv using Playwright and fetch images for a given Item (by id) or a direct URL.'

    def add_arguments(self, parser):
        parser.add_argument('--item-id', type=int, help='Item PK to fetch for')
        parser.add_argument('--url', help='Direct Pixiv artwork page URL to fetch')
        parser.add_argument('--headful', action='store_true', help='Run browser with UI (not headless)')

    def handle(self, *args, **options):
        if not HAVE_PLAYWRIGHT:
            self.stderr.write(self.style.ERROR('Playwright is not installed in this environment. Install with `pip install playwright` and run `playwright install` to install browsers.'))
            return

        item_id = options.get('item_id')
        url = options.get('url')
        headful = options.get('headful')

        if not item_id and not url:
            self.stderr.write(self.style.ERROR('Either --item-id or --url must be provided'))
            return

        pixiv_user = os.environ.get('PIXIV_USER') or os.environ.get('PIXIV_USERNAME')
        pixiv_pass = os.environ.get('PIXIV_PASS') or os.environ.get('PIXIV_PASSWORD')
        if not pixiv_user or not pixiv_pass:
            self.stderr.write(self.style.ERROR('Environment variables PIXIV_USER and PIXIV_PASS must be set for login'))
            return

        target_url = url
        it = None
        if item_id:
            try:
                it = Item.objects.get(pk=item_id)
                if not target_url:
                    target_url = it.link
            except Item.DoesNotExist:
                self.stderr.write(self.style.ERROR(f'Item with id={item_id} not found'))
                return

        if not target_url:
            self.stderr.write(self.style.ERROR('No URL available to fetch'))
            return

        self.stdout.write(self.style.NOTICE(f'Launching Playwright to fetch: {target_url}'))

        try:
            fetched = fetch_images_with_playwright(target_url, headful=headful)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Playwright fetch failed: {e}'))
            return

        if not fetched or not isinstance(fetched, dict):
            self.stderr.write(self.style.ERROR('No images fetched'))
            return

        logged = fetched.get('logged_in', False)
        images = fetched.get('images', [])

        self.stdout.write(self.style.NOTICE(f'Playwright reported logged_in={logged}; fetched {len(images)} image(s)'))

        collected = []
        # images are tuples (idx, body, content_type, url)
        for entry in images:
            if not entry or len(entry) < 4:
                continue
            idx, body, content_type, url_f = entry
            size = len(body) if body else 0
            self.stdout.write(self.style.NOTICE(f'Image idx={idx} size={size} bytes content_type={content_type} url={url_f}'))
            if item_id and it is not None:
                collected.append((idx, body, content_type, url_f))
            else:
                fname = f'/tmp/pw_fetched_{idx}.bin'
                with open(fname, 'wb') as f:
                    f.write(body)
                self.stdout.write(self.style.SUCCESS(f'Saved to {fname}'))

            # Now perform synchronous DB writes (after browser is closed) to avoid async context issues
            if item_id and it is not None:
                def _write():
                    PreviewImage.objects.filter(item=it).delete()
                    # pick the best (largest-bytes) image and save only that as order=0
                    best = max(collected, key=lambda t: len(t[1]) if t and t[1] else 0)
                    _, body, content_type, url_f = best
                    PreviewImage.objects.create(item=it, order=0, data=body, content_type=content_type)
                t = threading.Thread(target=_write)
                t.start()
                t.join()
                self.stdout.write(self.style.SUCCESS(f'Saved 1 PreviewImage for Item id={item_id}'))
            else:
                self.stdout.write(self.style.SUCCESS('Done (saved files to /tmp)'))
