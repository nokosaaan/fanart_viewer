import io
from django.core.management import call_command
from django.test import TestCase
from PIL import Image
from item.models import Item, PreviewImage
from item.image_optimizer import optimize_image_data


class ImageOptimizerTests(TestCase):
    def test_downscale_and_compress_large_image(self):
        # Create 3000x3000px image
        im = Image.new("RGB", (3000, 3000), color=(100, 150, 200))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=95)
        raw_jpeg = buf.getvalue()

        opt_bytes, ctype = optimize_image_data(raw_jpeg, "image/jpeg", max_dim=1000, quality=80)
        self.assertEqual(ctype, "image/webp")
        self.assertLess(len(opt_bytes), len(raw_jpeg))

        # Check dimensions of optimized image
        with Image.open(io.BytesIO(opt_bytes)) as opt_im:
            self.assertEqual(opt_im.format, "WEBP")
            self.assertLessEqual(opt_im.width, 1000)
            self.assertLessEqual(opt_im.height, 1000)

    def test_preserve_rgba_transparency(self):
        # Create transparent PNG
        im = Image.new("RGBA", (500, 500), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        raw_png = buf.getvalue()

        opt_bytes, ctype = optimize_image_data(raw_png, "image/png", max_dim=1000)
        self.assertEqual(ctype, "image/webp")

        with Image.open(io.BytesIO(opt_bytes)) as opt_im:
            self.assertEqual(opt_im.mode, "RGBA")

    def test_non_image_bytes_passthrough(self):
        dummy = b"not an image at all"
        opt_bytes, ctype = optimize_image_data(dummy, "image/jpeg")
        self.assertEqual(opt_bytes, dummy)
        self.assertEqual(ctype, "image/jpeg")

    def test_already_small_webp_passthrough(self):
        im = Image.new("RGB", (100, 100), color=(50, 50, 50))
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=80)
        raw_webp = buf.getvalue()

        opt_bytes, ctype = optimize_image_data(raw_webp, "image/webp", max_dim=2000)
        self.assertEqual(opt_bytes, raw_webp)
        self.assertEqual(ctype, "image/webp")

    def test_compress_previews_management_command(self):
        item = Item.objects.create(external_id=888801, link="https://example.com/test")
        im = Image.new("RGB", (2500, 2500), color=(200, 100, 50))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        raw_data = buf.getvalue()

        pi = PreviewImage.objects.create(item=item, order=0, data=raw_data, content_type="image/jpeg")
        orig_len = len(raw_data)

        # Run dry-run first
        out = io.StringIO()
        call_command("compress_previews", dry_run=True, stdout=out)
        pi.refresh_from_db()
        self.assertEqual(len(bytes(pi.data)), orig_len)
        self.assertEqual(pi.content_type, "image/jpeg")
        self.assertIn("DRY RUN", out.getvalue())

        # Run live run
        out_live = io.StringIO()
        call_command("compress_previews", max_dim=1000, quality=75, stdout=out_live)
        pi.refresh_from_db()
        self.assertLess(len(bytes(pi.data)), orig_len)
        self.assertEqual(pi.content_type, "image/webp")
        self.assertIn("Finished Preview Compression", out_live.getvalue())

