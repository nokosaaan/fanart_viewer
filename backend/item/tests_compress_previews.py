import io
import tempfile
from pathlib import Path
from PIL import Image
from django.core.management import call_command
from django.test import TestCase
from item.models import Item, PreviewImage


def _make_png_bytes(width=64, height=64, color=(200, 100, 50)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class CompressPreviewsCacheTests(TestCase):
    def test_cached_ids_are_skipped_on_second_run(self):
        item = Item.objects.create(
            external_id=9001,
            source="pixiv_bookmark",
            artist="Tester",
            link="https://www.pixiv.net/artworks/9001",
        )
        p = PreviewImage.objects.create(
            item=item,
            order=0,
            data=_make_png_bytes(256, 256),
            content_type="image/png",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / ".compress_previews_cache.json"
            from item.management.commands.compress_previews import Command

            orig_cache_path = Command._cache_file_path
            Command._cache_file_path = lambda self, _db: cache_file
            try:
                out1 = io.StringIO()
                call_command("compress_previews", stdout=out1)
                self.assertIn("cached-skip: 0, to-check: 1", out1.getvalue())
                self.assertTrue(cache_file.exists())

                out2 = io.StringIO()
                call_command("compress_previews", stdout=out2)
                self.assertIn("cached-skip: 1, to-check: 0", out2.getvalue())

                out3 = io.StringIO()
                call_command("compress_previews", "--no-cache", stdout=out3)
                self.assertIn("cached-skip: 0, to-check: 1", out3.getvalue())
            finally:
                Command._cache_file_path = orig_cache_path
