"""Character-tag -> series/title reverse lookup against Danbooru's public
API. The local tagger (see tagger.py) has no copyright/series tags in its
public vocabulary at all, and even its character recognition is limited to
whatever was in its training data as of the model's cutoff — recent titles
are frequently missing entirely. Danbooru's live tag data has no such
staleness problem, so when the tagger recognizes a character that doesn't
match anything in this app's own vocabulary yet, querying Danbooru for that
character's most common copyright (series) tag is a reliable way to name
a genuinely new title rather than just giving up.

This is the one part of the suggestion pipeline that makes an external network
call — everything else (tagger inference, DB history, tag similarity) stays
fully local. Callers gate this behind an explicit opt-in (see
views.suggest_tags_view's `external` flag) rather than running it
unconditionally.
"""
import logging

import requests

from .models import DanbooruTitleCache

logger = logging.getLogger(__name__)

_POSTS_ENDPOINT = "https://danbooru.donmai.us/posts.json"
_SAMPLE_SIZE = 20


def _to_danbooru_tag(character_name: str) -> str:
    """The tagger's character names use spaces ('hakurei reimu'); Danbooru's
    own tags use underscores ('hakurei_reimu')."""
    return character_name.strip().lower().replace(" ", "_")


def resolve_title_from_character(character_name: str) -> str | None:
    """Character name (tagger format, spaces) -> most common copyright/series
    tag among that character's recent Danbooru posts, or None if Danbooru
    had no clear answer or the request failed. Cached in DanbooruTitleCache
    (including negative results) so a given character is only ever looked
    up once.
    """
    tag = _to_danbooru_tag(character_name)
    if not tag:
        return None

    cached = DanbooruTitleCache.objects.filter(character_tag=tag).first()
    if cached is not None:
        return cached.title

    title = _query_danbooru(tag)
    DanbooruTitleCache.objects.update_or_create(character_tag=tag, defaults={'title': title})
    return title


def _query_danbooru(tag: str) -> str | None:
    try:
        resp = requests.get(
            _POSTS_ENDPOINT,
            params={"tags": tag, "limit": _SAMPLE_SIZE},
            headers={"User-Agent": "fanart-viewer/1.0 (personal archival tool)"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("danbooru_lookup: HTTP %s for tag=%s", resp.status_code, tag)
            return None
        posts = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("danbooru_lookup: request failed for tag=%s: %s", tag, e)
        return None

    if not isinstance(posts, list) or not posts:
        return None

    from collections import Counter

    counter = Counter()
    for post in posts:
        if not isinstance(post, dict):
            continue
        for copyright_tag in (post.get("tag_string_copyright") or "").split():
            counter[copyright_tag] += 1

    if not counter:
        return None

    top_tag, _count = counter.most_common(1)[0]
    return top_tag.replace("_", " ")
