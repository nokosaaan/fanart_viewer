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


# pykakasi mis-romanizes several loanword-style small-kana digraphs
# ("youon" combinations) common in katakana character names — it treats the
# small kana as its own separate vowel mora instead of merging it into a
# single consonant+vowel sound, e.g. シェリー ("Sherry") -> "shierii"
# instead of "sherii". Verified live: pykakasi gets ファ/フィ/フェ/フォ,
# ヴァ/ヴィ/ヴェ/ヴォ and チェ right, but gets シェ/ジェ/ティ/トゥ/ドゥ/
# ウィ/ウェ/ウォ/ツァ/クァ/グァ wrong, consistently. Each entry is (the
# katakana digraph, pykakasi's wrong romaji substring, the correct one) —
# the fix only fires when BOTH the source kana contains the digraph AND
# the raw romaji contains the exact wrong substring, so it can't
# accidentally rewrite an unrelated, natively-Japanese occurrence of the
# same letters elsewhere in a longer name.
_YOUON_ROMAJI_FIXES = (
    ('シェ', 'shie', 'she'), ('ジェ', 'jie', 'je'), ('ティ', 'tei', 'ti'),
    ('トゥ', 'tou', 'tu'), ('ドゥ', 'dou', 'du'),
    ('ウィ', 'ui', 'wi'), ('ウェ', 'ue', 'we'), ('ウォ', 'uo', 'wo'),
    ('ツァ', 'tsua', 'tsa'), ('クァ', 'kua', 'kwa'), ('グァ', 'gua', 'gwa'),
)


def _romaji(kana: str) -> str:
    """Best-effort katakana/hiragana -> lowercase romaji, apostrophes
    stripped (pykakasi renders a kana glottal stop like アンアン as
    "an'an" — the apostrophe is noise for the fuzzy comparison this feeds,
    see find_tag_via_title_roster), with _YOUON_ROMAJI_FIXES applied
    afterward for the specific digraphs pykakasi gets wrong (verified live
    — see that constant's comment for which ones and why)."""
    import pykakasi

    kks = pykakasi.kakasi()
    romaji = ''.join(r['hepburn'] for r in kks.convert(kana)).lower().replace("'", '')
    for digraph, wrong, right in _YOUON_ROMAJI_FIXES:
        if digraph in kana and wrong in romaji:
            romaji = romaji.replace(wrong, right)
    return romaji


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


def find_tag_via_title_roster(japanese_given_name: str, expected_titles):
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

    Returns (tag_or_None, debug_info). `debug_info` is a list with one entry
    per title actually evaluated — {'title', 'wiki_tag', 'roster_size',
    'top_scores': [(tag, score), ...] (top 5)} — for a caller to print and
    let a human sanity-check WHY a proposal was (or wasn't) made, since a
    wrong auto-applied fix is worse than no fix at all. `tag_or_None` is
    only ever the top-scored candidate for a confident, unambiguous match;
    None means every title's page either couldn't be resolved, had no
    parseable character roster, or no candidate across ALL titles was
    confident/distinct enough — never a guess.

    Evaluates EVERY title in `expected_titles` before picking a winner
    (does not stop at the first title whose best match clears the
    threshold) — an earlier version returned as soon as one title's roster
    produced a same-title-confident score, which broke on a real
    multi-title character: a coincidental 0.67 match against an unrelated
    70-person roster (from a title she's tagged with alongside her actual
    show) was returned before her real, much smaller-roster title was ever
    checked. Comparing every title's best candidate and keeping the
    globally highest-scoring one avoids exactly that failure mode — a
    genuine match is expected to score higher than an incidental one, but
    only if it's actually given the chance to be compared at all.
    """
    import difflib

    query_romaji = _romaji(japanese_given_name)
    debug_info = []
    if not query_romaji:
        return None, debug_info

    global_best = None  # (tag, score, roster_size)
    NEAR_TIE_MARGIN = 0.08  # see the loop's tie-break comment below
    for title in (expected_titles or []):
        page = _find_copyright_wiki_page(title)
        if not page:
            debug_info.append({'title': title, 'wiki_tag': None, 'roster_size': 0, 'top_scores': []})
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
            debug_info.append({'title': title, 'wiki_tag': page.get('title'), 'roster_size': len(roster), 'top_scores': []})
            continue

        scored = sorted(
            ((tag, difflib.SequenceMatcher(None, query_romaji, tag.rsplit('_', 1)[-1]).ratio()) for tag in roster),
            key=lambda x: -x[1],
        )
        debug_info.append({
            'title': title, 'wiki_tag': page.get('title'), 'roster_size': len(roster),
            'top_scores': scored[:5],
        })
        best_tag, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        # A real given name's romanization vs. Danbooru's own idiosyncratic
        # spelling can legitimately score closer to a runner-up than to a
        # neat margin (verified live: correct matches at 0.62/0.75 with a
        # same-roster runner-up at 0.55/0.67) — rejecting anything short of
        # a clear gap threw away good matches. Only reject on an outright
        # TIE for first place (a real ambiguity within THIS title's roster,
        # not just "close") or a score too low to be plausibly related at
        # all — but never return yet; keep checking every remaining title
        # so a stronger match elsewhere still wins.
        if best_score >= 0.4 and best_score > second_score:
            if global_best is None or best_score > global_best[1] + NEAR_TIE_MARGIN:
                global_best = (best_tag, best_score, len(roster))
            elif global_best is not None and abs(best_score - global_best[1]) <= NEAR_TIE_MARGIN:
                # Two different titles both produced a plausible match and
                # neither clearly beats the other — a larger roster
                # mechanically has more chances for some OTHER character's
                # name to coincidentally resemble the query than a small
                # one does (verified live: a 70-person roster's incidental
                # 0.73 outscored the actual 17-person roster's genuine
                # 0.67 for the same query), so prefer the smaller, more
                # specific roster on a near-tie rather than the raw
                # highest score.
                if len(roster) < global_best[2]:
                    global_best = (best_tag, best_score, len(roster))

    if global_best is not None:
        return global_best[0], debug_info
    return None, debug_info


def resolve_character_link(character_name: str, expected_titles=None):
    """Resolve one character's Danbooru tag via find_tag_via_title_roster
    and persist the result to CharacterDanbooruLink — the single-character
    unit of work shared by item.management.commands.link_danbooru_characters
    (bulk, offline) and item.views.CharacterDanbooruLinkViewSet.resolve
    (one character at a time, live from the frontend's own link-review UI).

    `expected_titles`: pass this in when the caller already has it (the
    bulk command scans every Item once for ALL characters up front —
    re-deriving it per-character here would turn an O(items) scan into
    O(items * characters)). Left as None for an ad-hoc single-character
    call (the frontend action), which scans Item just for this one name —
    fine at this app's item-count scale for a one-off click.

    Returns the CharacterDanbooruLink row (created or updated) — never
    raises for "no confident match" or "no titles", both are recorded as
    a normal (if unresolved) result, exactly like the bulk command's own
    per-character handling.
    """
    from .models import CharacterDanbooruLink, Item

    if expected_titles is None:
        titles = set()
        for item in Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'characters', 'titles',
        ).iterator():
            if character_name in (item.characters or []):
                titles.update(t for t in (item.titles or []) if t)
        expected_titles = sorted(titles)
    else:
        expected_titles = sorted(expected_titles)

    if not expected_titles:
        link, _ = CharacterDanbooruLink.objects.update_or_create(
            character_name=character_name,
            defaults={'danbooru_tag': None, 'resolved_via': '', 'match_score': None,
                      'debug_info': {'reason': 'no known titles for this character'}},
        )
        return link

    tag, debug_info = find_tag_via_title_roster(character_name, expected_titles)
    score = None
    if tag:
        for entry in debug_info:
            if entry.get('top_scores') and entry['top_scores'][0][0] == tag:
                score = entry['top_scores'][0][1]
                break

    link, _ = CharacterDanbooruLink.objects.update_or_create(
        character_name=character_name,
        defaults={
            'danbooru_tag': tag, 'resolved_via': 'title_roster' if tag else '',
            'match_score': score, 'debug_info': debug_info,
        },
    )
    return link


def dedupe_tag_collisions():
    """A single real Danbooru character can only ever be ONE of this app's
    own character names — two different names both resolving to the same
    tag means at least one is a wrong fuzzy-match collision, not a genuine
    alias (see link_danbooru_characters, which this was extracted from,
    for the real collision this was built to catch). The lower-scoring
    entry is demoted back to unresolved (never silently dropped — its
    debug_info is kept under 'original_debug_info' so a human can see what
    was rejected and why).

    Checks ALL CharacterDanbooruLink rows, not just recently-touched ones
    — cheap (one query, in-memory grouping) and a fresh resolution can
    newly collide with an old, previously-uncontested link either way.

    Returns [{'tag', 'winner', 'demoted': [name, ...]}, ...] — one entry
    per tag that had a collision, empty list if none did.
    """
    from collections import defaultdict

    from .models import CharacterDanbooruLink

    by_tag = defaultdict(list)
    for link in CharacterDanbooruLink.objects.exclude(danbooru_tag__isnull=True).exclude(danbooru_tag=''):
        by_tag[link.danbooru_tag].append(link)

    demotions = []
    for tag, links in by_tag.items():
        if len(links) < 2:
            continue
        links.sort(key=lambda l: -(l.match_score or 0))
        winner, losers = links[0], links[1:]
        for loser in losers:
            loser.danbooru_tag = None
            loser.resolved_via = ''
            loser.debug_info = {
                'demoted_reason': f'tag collision with {winner.character_name!r} (higher score)',
                'original_debug_info': loser.debug_info,
            }
            loser.save(update_fields=['danbooru_tag', 'resolved_via', 'debug_info'])
        demotions.append({'tag': tag, 'winner': winner.character_name, 'demoted': [l.character_name for l in losers]})
    return demotions


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
