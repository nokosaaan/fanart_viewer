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


_CHARACTER_LINK_RE = None  # compiled lazily — see _extract_roster_tags


def _romaji(kana: str) -> str:
    """Best-effort katakana/hiragana -> lowercase romaji, apostrophes
    stripped (pykakasi renders a kana glottal stop like アンアン as
    "an'an" — the apostrophe is noise for the fuzzy comparison this feeds,
    see find_tag_via_title_roster)."""
    import pykakasi

    kks = pykakasi.kakasi()
    return ''.join(r['hepburn'] for r in kks.convert(kana)).lower().replace("'", '')


def _tag_category(tag: str) -> int | None:
    """0=general, 3=copyright, 4=character (Danbooru's own tag category
    ids) for an EXACT tag name, or None if the tag doesn't exist."""
    try:
        resp = requests.get(
            _TAGS_ENDPOINT,
            params={"search[name]": tag},
            headers={"User-Agent": "fanart-viewer/1.0 (personal archival tool)"},
            timeout=10,
        )
        if not resp.ok:
            return None
        results = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(results, list) or not results:
        return None
    return results[0].get('category')


def _find_copyright_wiki_page(japanese_title: str) -> dict | None:
    """The Danbooru copyright (category=3) tag's wiki page whose
    other_names includes `japanese_title`, or None if there's zero or an
    ambiguous (2+) match. A title's own wiki page reliably lists its full
    cast as `[[Character Name]]` wiki-links (verified live against
    mahou_shoujo_no_majo_saiban's page) — see find_tag_via_title_roster,
    which uses that roster instead of a raw wildcard character-name search
    to avoid the common-given-name collisions across unrelated franchises
    that search proved to have (see this file's other functions' docs).
    """
    try:
        resp = requests.get(
            _WIKI_PAGES_ENDPOINT,
            params={"search[other_names_match]": f"*{japanese_title}*", "limit": 20},
            headers={"User-Agent": "fanart-viewer/1.0 (personal archival tool)"},
            timeout=10,
        )
        if not resp.ok:
            return None
        pages = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(pages, list):
        return None

    copyright_pages = [p for p in pages if isinstance(p, dict) and _tag_category(p.get('title', '')) == 3]
    if len(copyright_pages) == 1:
        return copyright_pages[0]
    return None


def _extract_roster_tags(wiki_body: str) -> list[str]:
    """[[Character Name]] / [[Character Name|...]] wiki-links from a
    copyright tag's wiki body, converted to Danbooru tag form
    ('Tachibana Sherry' -> 'tachibana_sherry'). Best-effort text parsing of
    a human-edited wiki body — not every title's page necessarily lists
    every character this way, hence the caller treating an empty result as
    "this approach doesn't apply here", not an error.
    """
    import re

    global _CHARACTER_LINK_RE
    if _CHARACTER_LINK_RE is None:
        _CHARACTER_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
    names = _CHARACTER_LINK_RE.findall(wiki_body or '')
    return [n.strip().lower().replace(' ', '_') for n in names if n.strip()]


def find_tag_via_title_roster(japanese_given_name: str, expected_titles) -> str | None:
    """Preferred over find_tag_via_other_names: resolves each of
    `expected_titles` to its Danbooru copyright wiki page, reads that
    page's own character roster (a title's wiki body reliably links its
    full cast — verified live), and fuzzy-matches `japanese_given_name`'s
    romanization against each roster member's given-name segment (the tag's
    last underscore-separated word — Danbooru character tags follow
    'familyname_givenname'). Matching within one title's small roster
    (typically single digits to a few dozen) sidesteps the ambiguity a
    global character-name search has: a common given name collides with
    dozens of unrelated franchises' characters (see find_tag_via_other_names's
    docstring), but within ONE cast list, given names are distinct enough
    that even a rough phonetic match (round-tripped kana romanization vs.
    Danbooru's own idiosyncratic spelling, e.g. "arisa" vs "alisa", "koko"
    vs "coco") reliably lands on the right person — verified against a real
    11-character roster, 11/11 correct even at similarity ratios as low as
    0.5.

    Returns the full Danbooru tag (e.g. 'tachibana_sherry') for a
    confident, unambiguous match, or None (this title's page couldn't be
    resolved, has no parseable roster, or the best match wasn't confident/
    distinct enough) — never a guess.
    """
    import difflib

    query_romaji = _romaji(japanese_given_name)
    if not query_romaji:
        return None

    for title in (expected_titles or []):
        page = _find_copyright_wiki_page(title)
        if not page:
            continue
        # A title's wiki body links plenty of non-character tags too
        # (genre, concepts, items, character songs, the copyright itself)
        # — verified live against mahou_shoujo_no_majo_saiban's page,
        # which links things like 'urban_fantasy' and 'gokuchou' alongside
        # its actual cast. Left in, these create coincidental near-ties
        # with the real character match, so only category=4 (character)
        # tags are kept as fuzzy-match candidates.
        roster = [tag for tag in _extract_roster_tags(page.get('body', '')) if _tag_category(tag) == 4]
        if len(roster) < 2:
            continue

        scored = sorted(
            ((tag, difflib.SequenceMatcher(None, query_romaji, tag.rsplit('_', 1)[-1]).ratio()) for tag in roster),
            key=lambda x: -x[1],
        )
        best_tag, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        # A real given name's romanization vs. Danbooru's own idiosyncratic
        # spelling can legitimately score closer to a runner-up than to a
        # neat margin (verified live: correct matches at 0.62/0.75 with a
        # same-roster runner-up at 0.55/0.67) — rejecting anything short of
        # a clear gap threw away good matches. Only reject on an outright
        # TIE for first place (a real ambiguity, not just "close") or a
        # score too low to be plausibly related at all.
        if best_score >= 0.4 and best_score > second_score:
            return best_tag

    return None


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
