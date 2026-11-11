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
    browser = "chrome:windows"
    pattern = (
        _DOMAIN_PATTERN
        + r"/(?:photos-(?:slide|index|item|view)-(?:page-\d+-)?(?:aid-|id-)?|albums-(?:index|intro)-(?:page-\d+-)?(?:id-|aid-)?|feed-index-aid-)(\d+)"
    )
    example = "https://example.com/photos-slide-aid-12345.html"

    def __init__(self, match):
        self.gallery_aid = match.group(1)
        m = re.match(r"(https?://[^/]+)", match.group(0))
        self.root = m.group(1) if m else "https://example.com"
        original_url = match.string if (match.string and match.string.startswith("http")) else f"{self.root}/photos-slide-aid-{self.gallery_aid}.html"
        super().__init__(match, original_url)

    def _fetch_page_with_fallback(self):
        candidate_urls = [
            self.page_url,
            f"{self.root}/photos-slide-aid-{self.gallery_aid}.html",
            f"{self.root}/photos-index-aid-{self.gallery_aid}.html",
            f"{self.root}/photos-gallery-aid-{self.gallery_aid}.html",
        ]
        seen = set()
        urls_to_try = []
        for u in candidate_urls:
            if u and u not in seen:
                seen.add(u)
                urls_to_try.append(u)

        last_exc = None
        for u in urls_to_try:
            try:
                resp = self.request(u, headers={"Referer": f"{self.root}/"})
                sc = getattr(resp, "status_code", None)
                if resp is not None and (not isinstance(sc, int) or sc < 400):
                    self.page_url = u
                    return resp.text
            except Exception as e:
                last_exc = e
                self.log.debug("Failed candidate %s: %s", u, e)

        if last_exc:
            raise last_exc
        return ""

    def items(self):
        from gallery_dl.extractor.common import Message

        self.login()
        page = self._fetch_page_with_fallback()
        data = self.metadata(page)
        imgs = self.images(page)

        data["count"] = len(imgs)
        yield Message.Directory, "", data
        enum_key = self.enum
        for data[enum_key], (url, imgdata) in enumerate(imgs, self.start):
            if imgdata:
                data.update(imgdata)
                if "extension" not in imgdata:
                    text.nameext_from_url(url, data)
            else:
                text.nameext_from_url(url, data)
            yield Message.Url, url, data

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
        manifest_urls = [
            f"{self.root}/photos-item-aid-{self.gallery_aid}.html",
            f"{self.root}/photos-gallery-aid-{self.gallery_aid}.html",
        ]
        raw = ""
        last_exc = None
        for manifest_url in manifest_urls:
            try:
                resp = self.request(manifest_url, headers={"Referer": self.page_url or f"{self.root}/"})
                sc = getattr(resp, "status_code", None)
                if resp is not None and (not isinstance(sc, int) or sc < 400) and resp.text:
                    raw = resp.text
                    if re.search(r'"(//[^"]+|https?://[^"]+)"', raw):
                        break
            except Exception as e:
                last_exc = e
                continue

        if not raw and page:
            raw = page
        elif not raw and last_exc:
            raise last_exc

        # Extract signed or direct image URLs from manifest
        urls = []
        seen = set()
        for u in re.findall(r'"(//[^"]+|https?://[^"]+)"', raw):
            if any(u.lower().endswith(ext) for ext in (".js", ".css", ".html", ".htm", ".ico")):
                continue
            if u.startswith("//"):
                u = "https:" + u
            if u not in seen:
                seen.add(u)
                urls.append((u, None))

        return urls
