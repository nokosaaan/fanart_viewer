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


_TAGS_ENDPOINT = "https://danbooru.donmai.us/tags.json"
_WIKI_PAGES_ENDPOINT = "https://danbooru.donmai.us/wiki_pages.json"


def tag_exists(tag: str) -> bool:
    """Whether `tag` (Danbooru underscore format, e.g. 'tachibana_sherry')
    is an EXACT, real Danbooru tag — search[name] is an exact-name lookup,
    not a substring/wildcard search (see search[name_matches]/wildcard
    endpoints for that, which name-collide too badly across unrelated
    franchises to be useful here — verified live: 'sherry*' alone matches
    dozens of unrelated characters). Used to audit whether a
    CharacterGroup's own alphabetized alias is actually a valid Danbooru
    tag (see item.management.commands.audit_character_aliases), not to
    guess one.
    """
    try:
        resp = requests.get(
            _TAGS_ENDPOINT,
            params={"search[name]": tag},
            headers={"User-Agent": "fanart-viewer/1.0 (personal archival tool)"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("danbooru_lookup.tag_exists: HTTP %s for tag=%s", resp.status_code, tag)
            return False
        results = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("danbooru_lookup.tag_exists: request failed for tag=%s: %s", tag, e)
        return False
    return isinstance(results, list) and len(results) > 0


def find_tag_via_other_names(japanese_name: str, expected_titles) -> str | None:
    """Best-effort: find the Danbooru character tag whose wiki page lists
    `japanese_name` as an other_name/alias AND whose wiki body references
    one of `expected_titles` — the title cross-check is essential, not
    optional: a bare Japanese first name (e.g. "シェリー") collides with
    dozens of unrelated characters across totally different franchises
    (verified live: Resident Evil, Detective Conan, Animal Crossing, JoJo,
    and more all have a character whose other_names includes exactly
    "シェリー") — without filtering by the title we already know this
    character belongs to, picking "the" match would be a coin flip at
    best. Returns the Danbooru tag name (e.g. 'tachibana_sherry') for
    exactly one unambiguous match, or None if there were zero or 2+
    surviving candidates after that filter (both cases need a human to
    decide, not a guess).
    """
    try:
        resp = requests.get(
            _WIKI_PAGES_ENDPOINT,
            params={"search[other_names_match]": f"*{japanese_name}*", "limit": 50},
            headers={"User-Agent": "fanart-viewer/1.0 (personal archival tool)"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("danbooru_lookup.find_tag_via_other_names: HTTP %s for name=%s",
                           resp.status_code, japanese_name)
            return None
        pages = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("danbooru_lookup.find_tag_via_other_names: request failed for name=%s: %s",
                       japanese_name, e)
        return None

    if not isinstance(pages, list):
        return None

    titles_lower = [t.lower() for t in (expected_titles or []) if t]
    if not titles_lower:
        return None  # nothing to disambiguate against — refuse to guess

    matches = [
        p for p in pages
        if isinstance(p, dict) and any(t in (p.get('body') or '').lower() for t in titles_lower)
    ]
    if len(matches) == 1:
        return matches[0].get('title')
    return None  # zero or ambiguous (2+) — a human should decide, not this function


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
