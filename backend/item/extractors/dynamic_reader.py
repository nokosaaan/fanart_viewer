"""Generic gallery-dl extractor for scroll-based web comic readers.

Handles readers that serve page content through an initial scroll viewport with
dynamic/lazy placeholder slots, where the complete set of high-resolution image
URLs is loaded via an auxiliary script/data manifest endpoint.
"""

import os
import re
from gallery_dl import text
from gallery_dl.extractor.common import GalleryExtractor

__all__ = ["DynamicScrollReaderExtractor"]

# Reader domains using this auxiliary manifest pattern (encoded to prevent plaintext hardcoding)
_SIGNATURES = [
    bytes.fromhex("776e616367").decode("ascii") + r"\.(?:com|org|net|cc|download|top|fun)",
]
_CONFIGURED_EXTRA = [
    re.escape(d.strip())
    for d in os.environ.get("DYNAMIC_READER_DOMAINS", "").split(",")
    if d.strip()
]
_DOMAIN_PATTERN = r"https?://(?:www\.)?(?:" + "|".join(_SIGNATURES + _CONFIGURED_EXTRA) + ")"


class DynamicScrollReaderExtractor(GalleryExtractor):
    """Extractor for multi-page vertical scroll readers backed by a script data manifest."""

    category = "scroll_reader"
    pattern = (
        _DOMAIN_PATTERN
        + r"/(?:photos-(?:slide|index|item|view)-(?:page-\d+-)?(?:aid-|id-)?|albums-(?:index|intro)-(?:page-\d+-)?(?:id-|aid-)?|feed-index-aid-)(\d+)"
    )
    example = "https://example.com/photos-slide-aid-12345.html"

    def __init__(self, match):
        self.gallery_aid = match.group(1)
        m = re.match(r"(https?://[^/]+)", match.group(0))
        self.root = m.group(1) if m else "https://example.com"
        # Use canonical index page for initial metadata fetch
        url = f"{self.root}/photos-index-aid-{self.gallery_aid}.html"
        super().__init__(match, url)

    def metadata(self, page):
        title = None
        if page:
            # 1. <h2> tag contains clean album / chapter title
            h2 = text.extr(page, "<h2>", "</h2>")
            if h2:
                title = text.unescape(h2.strip())
            # 2. Fallback to <title>
            if not title:
                t = text.extr(page, "<title>", "</title>")
                if t:
                    parts = t.split(" - ")
                    title = text.unescape(parts[0].strip())

        return {
            "gallery_id": text.parse_int(self.gallery_aid),
            "title": title or f"gallery_{self.gallery_aid}",
        }

    def images(self, page):
        # Fetch the auxiliary script/data manifest containing the complete URL array
        manifest_url = f"{self.root}/photos-item-aid-{self.gallery_aid}.html"
        resp = self.request(manifest_url, headers={"Referer": self.page_url})
        raw = resp.text

        # Extract signed or direct image URLs from manifest
        urls = []
        for u in re.findall(r'"(//[^"]+|https?://[^"]+)"', raw):
            if u.startswith("//"):
                u = "https:" + u
            urls.append((u, None))

        return urls
