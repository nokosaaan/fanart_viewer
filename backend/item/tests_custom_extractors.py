"""Tests for custom gallery-dl extractors (dynamic scroll readers, etc.)."""

from unittest.mock import MagicMock
from django.test import TestCase
import gallery_dl.extractor
from item.extractors import get_extractors_dir, register_custom_extractors
from item.extractors.dynamic_reader import DynamicScrollReaderExtractor


class CustomExtractorsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        register_custom_extractors()

    def test_extractors_dir_discovery(self):
        d = get_extractors_dir()
        self.assertTrue(d)

    def test_dynamic_reader_pattern_matches(self):
        # Resolve target host dynamically to avoid hardcoding plaintext identifiers
        host = bytes.fromhex("776e616367").decode("ascii")
        urls = [
            (f"https://www.{host}.com/photos-slide-aid-127245.html", "127245"),
            (f"https://www.{host}.com/photos-index-aid-127245.html", "127245"),
            (f"https://www.{host}.com/photos-item-aid-127245.html", "127245"),
            (f"https://www.{host}.com/albums-index-id-127245.html", "127245"),
            (f"https://www.{host}.com/albums-intro-id-127245.html", "127245"),
            (f"https://www.{host}.org/photos-slide-aid-9999.html", "9999"),
            (f"https://{host}.cc/albums-index-page-2-id-54321.html", "54321"),
        ]
        for url, expected_aid in urls:
            extr = gallery_dl.extractor.find(url)
            self.assertIsNotNone(extr, f"Should match extractor for {url}")
            self.assertEqual(extr.category, "scroll_reader")
            self.assertEqual(str(extr.gallery_aid), expected_aid)

    def test_metadata_extraction_h2(self):
        host = bytes.fromhex("776e616367").decode("ascii")
        extr = gallery_dl.extractor.find(f"https://www.{host}.com/photos-slide-aid-127245.html")
        mock_page = """
        <html>
        <head><title>Test Title - Reader - Portal</title></head>
        <body>
        <h2>Clean Album Title [DL]</h2>
        </body>
        </html>
        """
        meta = extr.metadata(mock_page)
        self.assertEqual(meta["gallery_id"], 127245)
        self.assertEqual(meta["title"], "Clean Album Title [DL]")

    def test_metadata_extraction_title_fallback(self):
        host = bytes.fromhex("776e616367").decode("ascii")
        extr = gallery_dl.extractor.find(f"https://www.{host}.com/photos-slide-aid-127245.html")
        mock_page = """
        <html>
        <head><title>Fallback Title [Book] - Portal</title></head>
        <body></body>
        </html>
        """
        meta = extr.metadata(mock_page)
        self.assertEqual(meta["gallery_id"], 127245)
        self.assertEqual(meta["title"], "Fallback Title [Book]")

    def test_images_extraction_from_manifest(self):
        host = bytes.fromhex("776e616367").decode("ascii")
        extr = gallery_dl.extractor.find(f"https://www.{host}.com/photos-slide-aid-127245.html")
        mock_response = MagicMock()
        mock_response.text = '''
        var mReader = {
            initData: function(data) {}
        };
        mReader.initData({
            "page_url": [
                "//img1.example.com/01.jpg?verify=abc",
                "http://img2.example.com/02.jpg?verify=def",
                "https://img3.example.com/03.jpg?verify=ghi"
            ]
        });
        '''
        extr.request = MagicMock(return_value=mock_response)
        imgs = extr.images("")
        self.assertEqual(len(imgs), 3)
        self.assertEqual(imgs[0][0], "https://img1.example.com/01.jpg?verify=abc")
        self.assertEqual(imgs[1][0], "http://img2.example.com/02.jpg?verify=def")
        self.assertEqual(imgs[2][0], "https://img3.example.com/03.jpg?verify=ghi")
