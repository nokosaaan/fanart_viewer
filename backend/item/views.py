"""Cleaned views for the `item` app.

This module exposes a conservative `ItemViewSet` and a Python-native
`items_from_db` view that returns serialized items from the Django DB. The
older "from_rust" wording has been removed.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import HttpResponse, JsonResponse
from django.db.models import Q, Exists, OuterRef
from django.utils import timezone
from collections import Counter, defaultdict
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from .models import Item, PreviewImage, CharacterGroup, CharacterDanbooruLink, TitleDanbooruLink, CharacterAliasGroup, SocialFetchQueueItem, TwitterPollState
from .twitter_creds import has_credentials as _have_twitter_creds
from . import danbooru_lookup
from . import pixiv_salvage
from .character_groups import ensure_characters_grouped, response_fields as _group_response_fields
from .danbooru_lookup import resolve_title_from_character as _resolve_title_from_character
from .serializers import ItemSerializer, CharacterGroupSerializer, CharacterAliasGroupSerializer
from security.ssrf_guard import validate_url, SSRFError
import logging
import traceback
import unicodedata
import base64
from .utils import fetch_twitter_media_urls, fetch_twitter_media_urls_with_sources, get_last_api_response
import os
from .headless_fetch import fetch_rendered_media
from django.views.decorators.csrf import csrf_exempt
import threading
from types import SimpleNamespace
try:
    from .playwright_helper import fetch_images_with_playwright
    HAVE_PIXIV_PLAYWRIGHT = True
except Exception:
    HAVE_PIXIV_PLAYWRIGHT = False
try:
    from .ytdlp_fetch import fetch_twitter_media_ytdlp
    HAVE_YTDLP = True
except Exception:
    HAVE_YTDLP = False
try:
    from .twitter_gql_fetch import (
        fetch_twitter_media, fetch_account_retweets, fetch_account_bookmarks,
        fetch_tweet_description, TwitterAuthError,
    )
    HAVE_TWITTER_GQL = True
except Exception:
    HAVE_TWITTER_GQL = False
try:
    from .gallerydl_fetch import fetch_twitter_media_gallerydl
    HAVE_GALLERYDL = True
except Exception:
    HAVE_GALLERYDL = False
try:
    from .poipiku_fetch import fetch_poipiku_media
    HAVE_POIPIKU = True
except Exception:
    HAVE_POIPIKU = False
try:
    from . import tagger
    HAVE_TAGGER = True
except Exception:
    logging.exception('item.tagger failed to import; all image-based suggestions will be skipped')
    HAVE_TAGGER = False
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


MIN_IMAGE_FETCH_BYTES = 50000
# Sanity cap for ItemViewSet.create_manual's direct file upload — generous
# enough for any real fanart image, just guards against an accidental
# multi-hundred-MB upload.
MAX_MANUAL_UPLOAD_BYTES = 25 * 1024 * 1024


def _fetch_image_via_requests(url, min_size=None):
    """Fetch a single URL via server-side requests.

    Responsibilities:
    - Perform a single HTTP GET with a conservative User-Agent.
    - Validate response is an image and not SVG.
    - Optionally enforce a minimum size (bytes) when provided.
    - Return (content_bytes, mime) or (None, None) on any failure.
    This function is small and deterministic — the view orchestrates when
    and why to call it (HTML path, renderer path). Keeping it top-level
    makes the network I/O boundary explicit.
    """
    try:
        validate_url(url)
    except SSRFError:
        return None, None

    headers = {'User-Agent': 'fanart-viewer-bot/1.0'}
    try:
        import requests as _requests
        r = _requests.get(url, timeout=15, headers=headers, allow_redirects=True)
        ct = r.headers.get('content-type', '')
        if r.status_code == 200 and ct and ct.split(';', 1)[0].startswith('image'):
            mime = ct.split(';', 1)[0].lower()
            if mime == 'image/svg+xml':
                return None, None
            content = r.content
            if min_size is not None:
                try:
                    if len(content or b'') < int(min_size):
                        return None, None
                except Exception:
                    pass
            return content, mime
    except Exception:
        return None, None
    return None, None


def _normalize_lookup_url(url):
    try:
        parsed = urlparse(url or '')
        host = (parsed.netloc or '').lower()
        host = host[4:] if host.startswith('www.') else host
        if host == 'mobile.twitter.com':
            host = 'twitter.com'
        if host == 'mobile.x.com':
            host = 'x.com'
        path = (parsed.path or '').rstrip('/')
        return f'{parsed.scheme or "https"}://{host}{path}'
    except Exception:
        return (url or '').strip().rstrip('/')


def _find_item_by_url(url):
    normalized = _normalize_lookup_url(url)
    tweet_id = None
    try:
        match = re.search(r'/status/(\d+)', normalized)
        if match:
            tweet_id = match.group(1)
    except Exception:
        tweet_id = None

    if tweet_id:
        try:
            qs = Item.objects.filter(external_id=int(tweet_id))
            if qs.exists():
                return qs.order_by('id').first()
        except Exception:
            pass

        qs = Item.objects.filter(link__icontains=f'/status/{tweet_id}')
        if qs.exists():
            return qs.first()

    candidates = {normalized}
    try:
        parsed = urlparse(normalized)
        path = (parsed.path or '').rstrip('/')
        for host in ('twitter.com', 'x.com', 'www.twitter.com', 'www.x.com'):
            candidates.add(f'https://{host}{path}')
    except Exception:
        pass

    qs = Item.objects.filter(link__in=list(candidates))
    if qs.exists():
        return qs.first()

    if tweet_id:
        qs = Item.objects.filter(link__icontains=tweet_id)
        if qs.exists():
            return qs.first()

    return None


def _call_fetch_and_save_preview(item_id, data=None):
    """Invoke ItemViewSet.fetch_and_save_preview directly, bypassing DRF's
    normal request/response dispatch entirely — for callers with no real
    HTTP request to hand it (a background thread, the poll_twitter_updates/
    poll_pixiv_bookmarks management commands). Because dispatch() never
    runs, none of the attributes it would normally set on the view get set
    either; skipping `view.request` in particular crashes get_object() ->
    self.check_object_permissions(self.request, obj) with a plain
    AttributeError the instant ANY item is looked up this way (verified
    live — every poller-driven fetch was failing on exactly this before
    `request` was added here). This app has no DEFAULT_PERMISSION_CLASSES
    configured (see backend/settings.py's REST_FRAMEWORK), so the
    permission check itself is a no-op (AllowAny) either way — this is
    purely about the attribute existing for that check to run against at
    all, not about what it's actually used for.
    """
    request = SimpleNamespace(data=data or {}, query_params={})
    view = ItemViewSet()
    view.request = request
    view.kwargs = {'pk': str(item_id)}
    return view.fetch_and_save_preview(request, pk=item_id)


def _run_bookmark_fetch_job(item_id, target_url, data=None):
    """Run the slow bookmark fetch/save flow outside the request thread."""
    try:
        _call_fetch_and_save_preview(item_id, data)
    except Exception:
        logging.exception('Background bookmark fetch failed for item %s url=%s', item_id, target_url)



def _run_account_retweets_job(screen_name, max_items):
    """Scan `screen_name`'s timeline for native retweets and archive each one
    not already in the DB, outside the request thread (this can take a
    while: a handful of GraphQL page requests plus one CDN download per
    image).

    Unlike _run_bookmark_fetch_job, this does NOT re-invoke
    fetch_and_save_preview per item — fetch_account_retweets already pulled
    each retweet's media URLs and post text directly out of the UserTweets
    timeline response, so a second per-tweet TweetDetail call (the thing
    that actually burns through the rate limit one request per item) would
    be pure waste. Only plain CDN GETs to pbs.twimg.com happen per item here,
    which sit outside the GraphQL endpoint's separate rate-limit bucket.
    """
    try:
        result = fetch_account_retweets(screen_name, max_items=max_items)
    except Exception:
        logging.exception('Account retweets fetch failed for screen_name=%s', screen_name)
        return

    created, skipped, failed = 0, 0, 0
    for rt in result.get('retweets', []):
        outcome = _archive_social_candidate(rt, source='twitter_rt')
        if outcome == 'created':
            created += 1
        elif outcome == 'skipped':
            skipped += 1
        else:
            failed += 1

    logging.info(
        'Account retweets fetch for %s: created=%d skipped=%d failed=%d pages=%d',
        screen_name, created, skipped, failed, result.get('pages_fetched', 0),
    )


def _archive_social_candidate(cand, source):
    """Create an Item (+ download its images) for one candidate dict, as
    produced by either twitter_gql_fetch.fetch_account_retweets
    ('retweets' entries) or fetch_account_bookmarks/fetch_account_likes
    (their own return lists) — all three share the exact same shape:
    {'tweet_id', 'screen_name', 'media_urls', 'description'}.

    Shared by every "auto" background scan (_run_account_retweets_job,
    _run_account_bookmarks_job) and their "manual review" queue-mode
    siblings (scan_account_retweets_view, scan_account_bookmarks_view) so
    all of them dedupe/create/download identically — only `source`
    differs ('twitter_rt' / 'twitter_bookmark' / 'twitter_like').

    `screen_name` here is deliberately NEVER the polled/scanned account —
    only the tweet's original author, or blank if that couldn't be read
    from the API response (Twitter occasionally omits it). A blank artist
    is left for the user to fill in later via the edit queue rather than
    silently guessing wrong.

    Returns 'created', 'skipped' (already archived), or 'failed'.
    """
    tweet_id = cand.get('tweet_id')
    author = cand.get('screen_name') or ''
    url = f'https://x.com/{author}/status/{tweet_id}' if author else f'https://x.com/i/status/{tweet_id}'

    if _find_item_by_url(url):
        return 'skipped'

    try:
        item = Item.objects.create(
            external_id=int(tweet_id),
            source=source,
            situation='',
            titles=[],
            characters=[],
            artist=author,
            link=url,
            tags=None,
            description=cand.get('description') or '',
        )
    except Exception:
        logging.exception('Failed to create Item for tweet %s (source=%s)', tweet_id, source)
        return 'failed'

    saved_any = False
    for idx, media_url in enumerate(cand.get('media_urls') or []):
        try:
            body, ctype = _fetch_image_via_requests(media_url, min_size=MIN_IMAGE_FETCH_BYTES)
            if body and ctype:
                PreviewImage.objects.create(item=item, order=idx, data=body, content_type=ctype)
                saved_any = True
        except Exception:
            logging.exception('Failed to download media %s for tweet %s', media_url, tweet_id)

    if saved_any:
        return 'created'
    # Nothing downloadable (dead CDN link, transient error) — drop the
    # empty Item rather than leave a preview-less row behind.
    item.delete()
    return 'failed'


# Sources counted as "already known" when deciding where fetch_account_
# bookmarks should stop paging — mirrors poll_twitter_updates.py's own
# _KNOWN_TWITTER_SOURCES constant (kept as a separate copy rather than
# imported from there: a management command module is the wrong direction
# to import business logic FROM into the main views module).
_TWITTER_SOURCES_FOR_DEDUPE = ['twitter_bookmark', 'twitter_like', 'twitter_rt']


def _known_twitter_ids():
    known_ids = set(
        Item.objects.filter(source__in=_TWITTER_SOURCES_FOR_DEDUPE)
        .values_list('external_id', flat=True)
    )
    known_ids |= set(SocialFetchQueueItem.objects.values_list('external_id', flat=True))
    return known_ids


def _get_bookmarks_resume_cursor():
    """The same TwitterPollState.bookmarks_resume_cursor poll_twitter_
    updates.py's own recurring discovery reads/writes (see that model
    field's own docstring) — shared here so a manual bulk-fetch continues
    from wherever the poller's own incremental catch-up currently stands,
    rather than restarting from the newest bookmark and re-walking ground
    the poller already covered.
    """
    state, _ = TwitterPollState.objects.get_or_create(pk=1)
    return state.bookmarks_resume_cursor or None


def _save_bookmarks_resume_cursor(resume_cursor):
    """Persist the frontier a bookmark fetch (manual or the poller's own)
    reached back to the shared TwitterPollState row, so whichever one runs
    next — poller tick or another manual fetch — picks up from here
    instead of either re-scanning already-covered ground or leaving a gap.
    `resume_cursor` is None once a fetch actually reaches already-known
    content or the true end of the timeline (i.e. genuinely caught up —
    see _fetch_social_timeline's own docstring), at which point this
    correctly clears the field back to '' so the next run starts fresh
    from the newest bookmark again.
    """
    state, _ = TwitterPollState.objects.get_or_create(pk=1)
    state.bookmarks_resume_cursor = resume_cursor or ''
    state.save(update_fields=['bookmarks_resume_cursor'])


def _run_account_bookmarks_job(max_pages):
    """Auto-mode background bookmark catch-up — mirrors
    _run_account_retweets_job exactly, just for the logged-in account's own
    bookmarks (session-based; no screen_name needed) instead of a scanned
    account's public timeline. Exists alongside poll_twitter_updates.py's
    own automatic discovery (which does this on every tick) for a one-off,
    on-demand "fetch everything pending right now" catch-up — most useful
    right after the poller itself has been unable to run (e.g. an auth
    failure) and a backlog has piled up.

    Shares its pagination frontier with the poller (see _get_bookmarks_
    resume_cursor/_save_bookmarks_resume_cursor) — this manual catch-up
    picks up wherever the poller's own incremental progress currently
    stands (skipping IDs the poller is already responsible for) and pushes
    that frontier further back in one go, rather than duplicating whatever
    ground the poller has already covered.
    """
    known_ids = _known_twitter_ids()
    try:
        candidates, resume_cursor = fetch_account_bookmarks(
            known_ids, max_pages=max_pages, start_cursor=_get_bookmarks_resume_cursor(),
        )
    except Exception:
        logging.exception('Account bookmarks fetch failed')
        return
    _save_bookmarks_resume_cursor(resume_cursor)

    created, skipped, failed = 0, 0, 0
    for cand in candidates:
        outcome = _archive_social_candidate(cand, source='twitter_bookmark')
        if outcome == 'created':
            created += 1
        elif outcome == 'skipped':
            skipped += 1
        else:
            failed += 1

    logging.info(
        'Account bookmarks fetch: created=%d skipped=%d failed=%d (max_pages=%d)',
        created, skipped, failed, max_pages,
    )


def _normalize_char_name(name):
    return re.sub(r'\s+', ' ', (name or '').strip().lower().replace('_', ' '))


# Below this, a CharacterDanbooruLink is stored but not trusted for
# production matching — see link_danbooru_characters' own review process
# (a wrong tag collision on the SAME tag is caught automatically there,
# but a merely-low-confidence match to a DIFFERENT tag isn't, and is left
# for human review rather than being silently applied here).
_DANBOORU_LINK_MIN_SCORE = 0.6


def _match_tagger_characters(candidates):
    """Cross-reference raw tagger character-tag names (Danbooru-style,
    lowercase/space-separated English) against the app's own character
    vocabulary, so a match reuses the EXISTING spelling/casing (avoiding
    near-duplicate strings like "hakurei reimu" vs "Hakurei Reimu") and,
    when the match belongs to a CharacterGroup linked to one or more
    titles, surfaces those as title suggestions too — backfilling the
    tagger's inherent gap (its public tag list has no copyright/series
    tags at all, so it can never suggest a title on its own).

    Two ways a candidate can match:
    1. Same-spelling after normalization (a DB character name that's
       already itself in Danbooru's romaji form).
    2. Via CharacterDanbooruLink — bridges e.g. a Japanese-named existing
       entry ("博麗霊夢") to the tagger's own romaji output ("hakurei
       reimu"), which the identity-only normalization in (1) alone can
       never do (see link_danbooru_characters, which populates this table
       from Danbooru's own per-title character rosters, not a guess made
       here). Only links at or above _DANBOORU_LINK_MIN_SCORE are used —
       lower-confidence ones are kept in the table for a human to review,
       not applied automatically.

    Either way, if the matched character belongs to a CharacterGroup, that
    group's `titles` are surfaced as title suggestions too — backfilling
    the tagger's inherent gap (its public tag list has no copyright/series
    tags at all, so it can never suggest a title on its own). `titles` is
    empty on every real CharacterGroup in this DB as of this writing
    (verified live), so this falls back to `group.name` ONLY when it's a
    confirmed exact match against this DB's own known title vocabulary —
    verified live that 37 of 43 real CharacterGroups name themselves
    exactly after one real title this way, but the other 6 are genuinely
    a genre/brand/franchise umbrella (e.g. "Fate", spanning several
    distinct real titles) where treating group.name as a title would be
    wrong — exactly the risk of CharacterGroup's free-form naming this
    fallback exists to avoid guessing past.

    `matched` on each returned item tells the caller which is which.
    `raw_name`/`match_method` ('direct'|'danbooru_link'|None) are carried
    through so a caller building a diagnostic breakdown (see
    _character_breakdown) can show a human WHY a suggestion came out the
    way it did — the tagger's own raw output vs. what it was resolved to,
    and via which route — so a wrong suggestion can be traced to the
    tagger's recognition itself, the Danbooru link table, or (if
    unmatched) neither ever having a chance to fire at all.
    """
    existing_by_norm = {}
    group_by_char_norm = {}
    for group in CharacterGroup.objects.all():
        for c in (group.characters or []):
            # A CharacterGroup is a classification bucket (which title, or
            # more broadly genre/brand/medium, a character belongs to) —
            # NOT a per-character alias list. Each entry in `characters` is
            # its own distinct real character, so a match must resolve to
            # THAT entry's own spelling, never to group.name (which is the
            # classification's own label, e.g. a genre or brand name, and
            # would be nonsense to show as a "character").
            norm_c = _normalize_char_name(c)
            existing_by_norm.setdefault(norm_c, []).append((c, group))
            group_by_char_norm[norm_c] = group
    for item in Item.objects.only('characters'):
        for c in (item.characters or []):
            if c:
                existing_by_norm.setdefault(_normalize_char_name(c), []).append((c, None))

    danbooru_link_by_norm = {}
    for link in CharacterDanbooruLink.objects.exclude(danbooru_tag__isnull=True).exclude(danbooru_tag=''):
        if (link.match_score or 0) >= _DANBOORU_LINK_MIN_SCORE:
            danbooru_link_by_norm[_normalize_char_name(link.danbooru_tag)] = link.character_name

    known_titles = set()
    for titles in Item.objects.exclude(titles=[]).exclude(titles__isnull=True).values_list('titles', flat=True):
        known_titles.update(titles or [])

    def titles_for_group(group):
        """Walks from `group` UP through its parent chain (see
        CharacterGroup.parent — mirrors Danbooru's own wiki hierarchy,
        e.g. a franchise like "Muv-Luv" with a narrower sub-title like
        "Muv-Luv Girls Garden" under it), returning the titles of the
        FIRST ancestor (starting at the group itself) that resolves one —
        via its own `titles` field, or the exact-known-title name fallback.
        A character assigned to a specific sub-title's group resolves
        there directly; one assigned only at a broader/franchise level
        (nothing more specific known) resolves against that broader
        group instead once it climbs to it — still a real, just less
        specific, title, not a guess. `seen` bounds the walk against a
        cyclic `parent` chain (validated against at write time — see
        CharacterGroupSerializer.validate_parent — but the ORM enforces
        nothing here, so this is a defensive backstop, not just a
        formality)."""
        node, seen = group, set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            if node.titles:
                return node.titles
            if node.name in known_titles:
                return [node.name]
            node = node.parent
        return []

    matched, unmatched = [], []
    suggested_titles = set()
    for cand in candidates:
        norm = _normalize_char_name(cand['name'])
        hits = existing_by_norm.get(norm)
        if hits:
            existing_name, group = hits[0]
            matched.append({
                'name': existing_name, 'score': cand['score'], 'matched': True,
                'raw_name': cand['name'], 'match_method': 'direct',
            })
            suggested_titles.update(titles_for_group(group))
            continue
        linked_name = danbooru_link_by_norm.get(norm)
        if linked_name:
            matched.append({
                'name': linked_name, 'score': cand['score'], 'matched': True,
                'raw_name': cand['name'], 'match_method': 'danbooru_link',
            })
            suggested_titles.update(titles_for_group(group_by_char_norm.get(_normalize_char_name(linked_name))))
            continue
        unmatched.append({
            'name': cand['name'], 'score': cand['score'], 'matched': False,
            'raw_name': cand['name'], 'match_method': None,
        })

    return matched + unmatched, sorted(suggested_titles)


# Cap on how many not-directly-matching hashtags get a Danbooru alias
# lookup per call — most hashtags in a real post are spoiler/series tags
# that were never going to resolve to a character alias at all (DB-cached
# as a negative after the first miss, but that doesn't help the very FIRST
# time a given post's hashtags are seen), so this bounds worst-case Danbooru
# calls for one suggestion request rather than firing one per hashtag.
_HASHTAG_ALIAS_LOOKUP_CAP = 5


_HASHTAG_TOKEN_RE = re.compile(r'[#\uff03]([^\s#\uff03\u3001\u3002,.!\uff01?\uff1f\u300c\u300d\u300e\u300f"\'\u2019\u201d\u2026:\uff1a;\uff1b]+)')
_TRAILING_QUALIFIER_RE = re.compile(r'^(.*?)\s*[(\uff08]([^()\uff08\uff09]+)[)\uff09]\s*$')


def _extract_hashtag_tokens(description):
    """Raw hashtag strings from the post text, original case, parentheses
    kept intact (unlike _extract_hashtags, whose `\\w+` stops at the first
    "(") -- Pixiv's own convention for tagging a character is
    "#name(title)", which needs the qualifier to survive extraction."""
    if not description:
        return []
    seen, out = set(), []
    for tok in _HASHTAG_TOKEN_RE.findall(description):
        # a ")" the author opened outside the tag, e.g. "(#foo)"
        while tok and tok[-1] in ')\uff09' and (tok.count('(') + tok.count('\uff08')) < (tok.count(')') + tok.count('\uff09')):
            tok = tok[:-1]
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _fold_name(s):
    """Script-insensitive comparison key: NFKC (full/half-width), casefold,
    katakana -> hiragana, Latin diacritics stripped (Pokemon/Pok\u00e9mon),
    and everything that isn't a letter/digit dropped (spaces, "\u30fb", "_",
    "-", brackets). Diacritics are only stripped from Latin-range letters --
    NFKD-ing kana would detach dakuten and change the word."""
    s = unicodedata.normalize('NFKC', s or '').casefold()
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            ch = chr(o - 0x60)
        elif 0x80 <= o < 0x250:
            ch = ''.join(c for c in unicodedata.normalize('NFKD', ch) if not unicodedata.combining(c))
        out.extend(c for c in ch if unicodedata.category(c)[0] in ('L', 'N'))
    return ''.join(out)


def _name_variants(name):
    """(full, base, qualifier) folded keys. base/qualifier are None unless
    the name ends in a "(...)" part -- "tairitsu (arcaea)" or a hashtag
    "\u30ea\u30fc\u30ea\u30a8(\u30dd\u30b1\u30e2\u30f3)" -> base "\u30ea\u30fc\u30ea\u30a8", qualifier "\u30dd\u30b1\u30e2\u30f3"."""
    raw = unicodedata.normalize('NFKC', name or '').strip()
    full = _fold_name(raw)
    m = _TRAILING_QUALIFIER_RE.match(raw)
    if not m:
        return full, None, None
    return full, (_fold_name(m.group(1)) or None), (_fold_name(m.group(2)) or None)


_PARTIAL_MIN_LEN_CJK = 3
_PARTIAL_MIN_LEN_ASCII = 4
_PARTIAL_MIN_LEN_HAN = 2
_PARTIAL_MIN_RATIO = 0.15


def _is_partial_match(a, b):
    """Whether the shorter of two folded keys sits inside the longer one
    (e.g. a hashtag "\u30dd\u30b1\u30e2\u30f3\u4eba\u9593\u7d75" around the title "\u30dd\u30b1\u30e2\u30f3"). Guarded
    against short-name false positives: a minimum length (shorter for CJK,
    where one character carries more information; 2 for an all-kanji name),
    a minimum length ratio,
    and a stoplist of bare colour words ("white" is a real trait-bucket
    character in this DB, and "#whitehair" must not suggest it)."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not short or short == long_ or short not in long_:
        return False
    if short in {_fold_name(w) for w in _COLOR_WORDS}:
        return False
    is_ascii = short.isascii()
    # An all-kanji name carries more information per character than kana
    # does ("\u611b\u97f3" inside "#\u5343\u65e9\u611b\u97f3\u751f\u8a95\u796d2025" is a real hit; a 2-kana name is not).
    is_han = all('\u4e00' <= ch <= '\u9fff' for ch in short)
    min_len = _PARTIAL_MIN_LEN_ASCII if is_ascii else (_PARTIAL_MIN_LEN_HAN if is_han else _PARTIAL_MIN_LEN_CJK)
    if len(short) < min_len:
        return False
    return len(short) / len(long_) >= _PARTIAL_MIN_RATIO


def _build_fold_index(names_with_extra_keys):
    """{folded key: canonical name}. `names_with_extra_keys` is an iterable
    of (canonical_name, [extra keys already folded]). The full-name key
    keeps the first registration (as the old normalized-string match did);
    base-name keys are only registered when exactly one distinct name owns
    them -- "foo (a)" and "foo (b)" both folding to "foo" must not make "foo"
    resolve to whichever came first."""
    full_index, base_owners = {}, {}
    for name, extra in names_with_extra_keys:
        full, base, _qual = _name_variants(name)
        if full:
            full_index.setdefault(full, name)
        for key in ([base] if base else []) + list(extra):
            if key:
                base_owners.setdefault(key, set()).add(name)
    index = dict(full_index)
    for key, owners in base_owners.items():
        if key not in index and len(owners) == 1:
            index[key] = next(iter(owners))
    return index


def _match_hashtags(description, external=False):
    """Match hashtags from the source post's own text (see Item.description)
    against the app's existing title/character vocabulary. This is the
    single most reliable signal available when present -- the artist's own
    naming, not an inference -- so it's tried first, independently of (and
    before) any image analysis.

    Matching is done on a folded key (_fold_name): full/half-width,
    hiragana/katakana, case, diacritics and punctuation no longer matter.
    Three tiers:
    - exact: a hashtag's folded form (or, for "name(title)" hashtags, the
      name part) equals a registered name's folded form. A "(title)"
      qualifier is matched against registered titles as exact too -- the
      artist stating the title outright.
    - romaji <-> Japanese: a CharacterDanbooruLink (score >=
      _DANBOORU_LINK_MIN_SCORE) maps the Danbooru tag spelling to the
      registered name, so an English hashtag resolves to a Japanese-
      registered character and vice versa.
    - partial (returned separately as titles_partial/characters_partial,
      for the weaker 'hashtag_partial' ensemble source): one side contains
      the other, subject to _is_partial_match's guards.

    `external`: when true, a hashtag that didn't match directly also gets a
    Danbooru wiki-alias lookup (see danbooru_lookup.find_registered_
    character_via_alias) -- the one network call here, gated behind the
    same explicit opt-in as the rest of this pipeline's Danbooru use.
    Capped at _HASHTAG_ALIAS_LOOKUP_CAP per call and cached per hashtag
    text (DanbooruAliasCache).
    """
    empty = {'titles': [], 'characters': [], 'titles_partial': [], 'characters_partial': []}
    tokens = _extract_hashtag_tokens(description)
    if not tokens:
        return empty

    exact_keys, qualifier_keys, partial_keys = set(), set(), set()
    for tok in tokens:
        full, base, qual = _name_variants(tok)
        for k in (full, base):
            if k:
                exact_keys.add(k)
                partial_keys.add(k)
        if qual:
            qualifier_keys.add(qual)
    if not exact_keys and not qualifier_keys:
        return empty

    title_names = set()
    for titles in Item.objects.exclude(titles=[]).values_list('titles', flat=True):
        for name in (titles or []):
            if name:
                title_names.add(name)
    title_link_keys = {}
    for tl in TitleDanbooruLink.objects.exclude(danbooru_tag__isnull=True).exclude(danbooru_tag=''):
        if (tl.match_score or 0) >= _DANBOORU_LINK_MIN_SCORE and tl.title_name in title_names:
            full, base, _q = _name_variants(tl.danbooru_tag)
            title_link_keys.setdefault(tl.title_name, []).extend(k for k in (full, base) if k)
    title_index = _build_fold_index((n, title_link_keys.get(n, [])) for n in title_names)

    link_keys_by_name = {}
    for link in CharacterDanbooruLink.objects.exclude(danbooru_tag__isnull=True).exclude(danbooru_tag=''):
        if (link.match_score or 0) >= _DANBOORU_LINK_MIN_SCORE:
            full, base, _q = _name_variants(link.danbooru_tag)
            link_keys_by_name.setdefault(link.character_name, []).extend(k for k in (full, base) if k)

    char_names = []
    for group in CharacterGroup.objects.all():
        char_names.extend(c for c in (group.characters or []) if c)
    for chars in Item.objects.exclude(characters=[]).values_list('characters', flat=True):
        char_names.extend(c for c in (chars or []) if c)
    unique_char_names = list(dict.fromkeys(char_names))
    char_index = _build_fold_index((n, link_keys_by_name.get(n, [])) for n in unique_char_names)

    matched_titles = {title_index[k] for k in (exact_keys | qualifier_keys) if k in title_index}
    matched_chars = {char_index[k] for k in exact_keys if k in char_index}

    partial_titles, partial_chars = set(), set()
    for hk in partial_keys:
        for key, name in title_index.items():
            if name not in matched_titles and _is_partial_match(hk, key):
                partial_titles.add(name)
        for key, name in char_index.items():
            if name not in matched_chars and _is_partial_match(hk, key):
                partial_chars.add(name)

    if external:
        unmatched = []
        for tok in tokens:
            full, base, _q = _name_variants(tok)
            if not any(k in char_index for k in (full, base) if k):
                m = _TRAILING_QUALIFIER_RE.match(unicodedata.normalize('NFKC', tok))
                unmatched.append((m.group(1) if m else tok).lower())
        for h in unmatched[:_HASHTAG_ALIAS_LOOKUP_CAP]:
            try:
                bridged = danbooru_lookup.find_registered_character_via_alias(h, unique_char_names)
            except Exception:
                logging.exception('Danbooru alias lookup failed for hashtag %r', h)
                continue
            if bridged:
                matched_chars.add(bridged)
                partial_chars.discard(bridged)

    return {
        'titles': sorted(matched_titles),
        'characters': sorted(matched_chars),
        'titles_partial': sorted(partial_titles - matched_titles),
        'characters_partial': sorted(partial_chars - matched_chars),
    }


# An artist needs at least this many OTHER tagged items before their history
# counts as a real pattern rather than noise from one or two data points.
_ARTIST_HISTORY_MIN_SAMPLES = 2
# A title/character/situation must show up in at least this fraction of an
# artist's other tagged items to be suggested — a single one-off elsewhere
# isn't a strong enough signal.
_ARTIST_HISTORY_MIN_SHARE = 0.3


def _suggest_from_existing_data(item):
    """Suggest titles/characters/situation purely from the DB — no image
    analysis. Looks at this item's OTHER same-artist items that already
    have metadata filled in, and suggests whichever titles/characters/
    situation recur often enough among them (most artists repeatedly draw
    a small set of series/characters, so their own history is a strong,
    free prior). This is the primary suggestion source; the tagger model
    is only invoked as a fallback when this yields too little (see
    suggest_tags_view).
    """
    empty = {'titles': [], 'title_candidates': [], 'characters': [], 'situation_hint': None, 'sample_size': 0}
    if not item.artist:
        return empty

    siblings = list(Item.objects.filter(artist=item.artist).exclude(pk=item.pk).only('titles', 'characters', 'situation'))
    if len(siblings) < _ARTIST_HISTORY_MIN_SAMPLES:
        return empty

    title_counter, char_counter, situation_counter = Counter(), Counter(), Counter()
    for sib in siblings:
        title_counter.update(set(sib.titles or []))
        char_counter.update(set(sib.characters or []))
        if sib.situation:
            situation_counter[sib.situation] += 1

    # floor of 2, not 1 — a single stray mention among an artist's other
    # items isn't a real pattern, it's noise (this matters a lot for small
    # sample sizes, where share*n rounds down to 1 otherwise)
    min_count = max(2, round(len(siblings) * _ARTIST_HISTORY_MIN_SHARE))
    suggested_titles = [t for t, n in title_counter.most_common(5) if n >= min_count]
    # Low-confidence fallback: an artist who draws a handful of different
    # things won't have any title clear the bar above — offer the top
    # couple candidates anyway rather than nothing, for the user to pick
    # from (see suggest_tags_view's title_candidates handling).
    title_candidates = [t for t, _ in title_counter.most_common(3)] if not suggested_titles else []
    suggested_characters = [c for c, n in char_counter.most_common(10) if n >= min_count]
    top_situation = situation_counter.most_common(1)
    suggested_situation = top_situation[0][0] if top_situation and top_situation[0][1] >= min_count else None

    return {
        'titles': suggested_titles,
        'title_candidates': title_candidates,
        'characters': suggested_characters,
        'situation_hint': suggested_situation,
        'sample_size': len(siblings),
    }


# --- Weighted tag similarity -------------------------------------------
#
# Not every shared tag is equally good evidence of "same character". Eye
# color / hair color / worn accessories tend to stay consistent for a given
# character even across wildly different scenes, poses, and outfits-of-the-
# day — so they're weighted well above generic composition tags like
# "1girl"/"solo"/"outdoors". Hashtags pulled from the source post's own text
# (see Item.description) are weighted highest of all: they're the artist's
# own words, not an inference, and often directly name the character/work.

TAG_WEIGHT_HASHTAG = 5
TAG_WEIGHT_FEATURE = 3
TAG_WEIGHT_GENERIC = 1

_COLOR_WORDS = {
    'black', 'white', 'red', 'blue', 'green', 'yellow', 'pink', 'purple',
    'brown', 'orange', 'grey', 'gray', 'silver', 'blonde', 'blond', 'aqua',
    'violet', 'multicolored', 'platinum',
}

# Best-effort keyword list, not an exhaustive taxonomy — a tag containing
# any of these is treated as a worn accessory for weighting purposes.
_ACCESSORY_KEYWORDS = (
    'hair ornament', 'hairclip', 'hair clip', 'ribbon', 'necklace', 'earring',
    'glasses', 'eyewear', 'hat', 'headwear', 'hairband', 'hair band', 'choker',
    'bracelet', 'hair bow', 'brooch', 'hairpin', 'hair pin', 'jewelry',
    'accessory', 'collar', 'gloves', 'tiara', 'crown', 'headphones', 'mask',
    'earmuffs', 'hairpiece',
)

_HASHTAG_RE = re.compile(r'#(\w+)', re.UNICODE)


def _extract_hashtags(description):
    """Hashtags from the source post's own text — see Item.description."""
    if not description:
        return set()
    return {m.lower() for m in _HASHTAG_RE.findall(description) if m}


_OC_HASHTAGS = {'oc', 'original', 'originalcharacter', 'オリジナル', 'オリキャラ', '創作', '自創作', 'sozaku'}
_OC_SUBSTRINGS = ('オリジナルキャラ', 'オリキャラ', '創作キャラ', '自創作')


def _looks_like_oc(description):
    """Whether the source post's own text signals an original (non-licensed)
    character — the last-resort fallback in suggest_tags_view when nothing
    (hashtag match, DB history, tag similarity, tagger+CharacterGroup match,
    Danbooru reverse lookup) could name an existing title for this item.
    """
    if not description:
        return False
    if _extract_hashtags(description) & _OC_HASHTAGS:
        return True
    return any(s in description for s in _OC_SUBSTRINGS)


# Tags that describe the DRAWING rather than the character -- on a
# black-and-white image they fire for every character alike, so as shared
# tags they would make unrelated monochrome works look similar.
_MONOCHROME_STYLE_TAGS = {
    'monochrome', 'greyscale', 'grayscale', 'sketch', 'lineart', 'line art', 'comic', '4koma',
    'spot color', 'halftone', 'screentones', 'speech bubble', 'translated', 'text focus',
}

# Colour-independent identity cues: hairstyle/length and head features.
# Eye/hair COLOUR and worn accessories are the "feature" tags above -- on a
# black-and-white image colour can't be observed, so the shape of the hair
# has to carry the character-level evidence instead (weaker, but real).
_SHAPE_KEYWORDS = (
    'twintails', 'ponytail', 'braid', 'ahoge', 'bob cut', 'hair bun', 'double bun', 'drill hair',
    'hair over one eye', 'hair over eyes', 'side ponytail', 'low twintails', 'short hair', 'long hair',
    'medium hair', 'very long hair', 'absurdly long hair', 'bangs', 'blunt bangs', 'sidelocks', 'hime cut',
    'animal ears', 'cat ears', 'fox ears', 'wolf ears', 'rabbit ears', 'horns', 'pointy ears', 'elf', 'halo',
    'tail', 'wings', 'mole', 'fang', 'heterochromia',
)


def _is_color_feature_tag(tag):
    words = tag.split()
    return len(words) >= 2 and words[0] in _COLOR_WORDS and words[-1] in ('eyes', 'hair')


def _tag_weight(tag, monochrome=False):
    if _is_color_feature_tag(tag):
        return TAG_WEIGHT_FEATURE
    if any(kw in tag for kw in _ACCESSORY_KEYWORDS):
        return TAG_WEIGHT_FEATURE
    if monochrome and any(kw in tag for kw in _SHAPE_KEYWORDS):
        return TAG_WEIGHT_FEATURE
    return TAG_WEIGHT_GENERIC


def _weighted_tag_signature(tags, description, monochrome=False):
    """{tag_or_hashtag: weight} for one item, for scoring similarity
    against another item's signature.

    `monochrome`: build the signature for comparing a black-and-white image
    (see _suggest_from_similar_tags) -- colour tags and drawing-style tags
    are left out (they can't be observed / don't distinguish characters),
    and hairstyle-shape tags are promoted to feature weight."""
    sig = {}
    for t in (tags or []):
        if not t:
            continue
        if monochrome and (_is_color_feature_tag(t) or t in _MONOCHROME_STYLE_TAGS):
            continue
        sig[t] = max(sig.get(t, 0), _tag_weight(t, monochrome))
    for h in _extract_hashtags(description):
        sig[h] = max(sig.get(h, 0), TAG_WEIGHT_HASHTAG)
    return sig


# Minimum weighted overlap score before a DB item counts as "similar
# enough" to factor into tag-based suggestion at all — a couple of generic
# shared tags (e.g. "1girl", "solo", each worth 1 point) isn't a meaningful
# match, only a genuinely overlapping, specific combination is. Deliberately
# scored from the UNCAPPED tag list (tagger.py's `tags_full`, not the
# display-only `tags`) — more tags is a more specific, more reliable
# signal, and this list is never shown to the user as-is.
_TAG_SIMILARITY_MIN_SCORE = 6
_TAG_SIMILARITY_MIN_SAMPLES = 2
_TAG_SIMILARITY_MIN_SHARE = 0.3
# Characters need their own, stricter bar: a single shared high-priority
# attribute (say, just hair color) isn't enough to call it the same
# character — that's genuinely the "keeping X unique" ask this app cares
# about (avoid seeding wrong-but-plausible character guesses).
_CHARACTER_MIN_FEATURE_MATCHES = 2


def _suggest_from_similar_tags(tag_names, description, exclude_pk, monochrome=False):
    """Suggest titles/characters/situation from OTHER items whose (weighted)
    tags overlap substantially with this item's. Complements
    _suggest_from_existing_data's artist-based prior: an artist who draws
    many different things won't get a useful suggestion from "their other
    items", but items sharing a specific combination of visual tags
    plausibly depict the same character/series regardless of who drew them.

    `monochrome`: the query image is black-and-white, so its tags carry no
    colour information and a few drawing-style tags ("monochrome",
    "greyscale") that fire for every character. Both sides' signatures then
    drop colour and style tags and count hairstyle/head-shape tags as
    feature matches (see _weighted_tag_signature), so the "2 feature
    matches" bar for suggesting a character can still be met -- on shape
    instead of colour.

    Returns titles/title_candidates split: `titles` only holds candidates
    that cleared the normal confidence bar; when NONE do but there was
    still some signal, `title_candidates` holds a short, unranked-confidence
    list instead of nothing — better to let the user pick from a couple of
    plausible options than silently give up.
    """
    empty = {'titles': [], 'title_candidates': [], 'characters': [], 'situation_hint': None, 'sample_size': 0}
    query_sig = _weighted_tag_signature(tag_names, description, monochrome)
    plain_tags = [t for t in (tag_names or []) if t and (not monochrome or t in query_sig)]
    if not plain_tags or not query_sig:
        return empty

    # JSONField `contains` (checking whether any of plain_tags appears in
    # Item.tags) is a Postgres/MySQL-only lookup — SQLite raises
    # NotSupportedError for it (see backend.settings' DB_ENGINE toggle for
    # why SQLite needs to work here at all: the exe-packaged distribution's
    # per-user local DB). Filtering in Python instead, same as
    # region_mismatch_queue's own "can't express this as a portable query"
    # reasoning — fine at this app's personal-archive scale.
    plain_tags_set = set(plain_tags)
    candidates = [
        it for it in Item.objects.exclude(pk=exclude_pk)
        .only('tags', 'description', 'titles', 'characters', 'situation').iterator()
        if plain_tags_set & set(it.tags or [])
    ]

    # (score, feature_match_count, item) for every candidate that clears the
    # overlap-score bar. feature_match_count only counts tags that are
    # high-priority (feature/hashtag, weight >= TAG_WEIGHT_FEATURE) on BOTH
    # sides — a hashtag on our side matching a merely-generic tag on theirs
    # doesn't count as a "feature match", only a mutually-specific one does.
    scored = []
    for c in candidates:
        sib_sig = _weighted_tag_signature(c.tags, c.description, monochrome)
        shared = query_sig.keys() & sib_sig.keys()
        if not shared:
            continue
        score = sum(min(query_sig[t], sib_sig[t]) for t in shared)
        if score < _TAG_SIMILARITY_MIN_SCORE:
            continue
        feature_matches = sum(
            1 for t in shared
            if query_sig[t] >= TAG_WEIGHT_FEATURE and sib_sig[t] >= TAG_WEIGHT_FEATURE
        )
        scored.append((score, feature_matches, c))

    if len(scored) < _TAG_SIMILARITY_MIN_SAMPLES:
        return empty

    title_counter, situation_counter = Counter(), Counter()
    for _score, _fm, sib in scored:
        title_counter.update(set(sib.titles or []))
        if sib.situation:
            situation_counter[sib.situation] += 1

    # Characters only come from the subset with enough independent
    # high-priority attribute matches — see _CHARACTER_MIN_FEATURE_MATCHES.
    char_eligible = [sib for _score, fm, sib in scored if fm >= _CHARACTER_MIN_FEATURE_MATCHES]
    char_counter = Counter()
    for sib in char_eligible:
        char_counter.update(set(sib.characters or []))

    n = len(scored)
    min_count = max(2, round(n * _TAG_SIMILARITY_MIN_SHARE))
    suggested_titles = [t for t, cnt in title_counter.most_common(5) if cnt >= min_count]
    title_candidates = [t for t, _ in title_counter.most_common(3)] if not suggested_titles else []

    char_min_count = max(2, round(len(char_eligible) * _TAG_SIMILARITY_MIN_SHARE))
    suggested_characters = [c for c, cnt in char_counter.most_common(10) if cnt >= char_min_count] if char_eligible else []

    top_situation = situation_counter.most_common(1)
    suggested_situation = top_situation[0][0] if top_situation and top_situation[0][1] >= min_count else None

    return {
        'titles': suggested_titles,
        'title_candidates': title_candidates,
        'characters': suggested_characters,
        'situation_hint': suggested_situation,
        'sample_size': n,
    }


def _merge_unique(*lists):
    seen, out = set(), []
    for lst in lists:
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def _select_image_bytes(item, image_index=None):
    """Pick which of `item`'s images to feed the tagger for inference.

    `image_index`: 0-based position in item.preview_images ordered by
    `order` — the same indexing scheme as ItemViewSet.preview's own
    `?index=N` query param (and previews/{idx}/), so a caller can request
    inference on a specific image the user is looking at in the preview
    carousel. Out of range or None falls back to the original default:
    the single largest-by-byte-size PreviewImage (a reasonable pick when
    the caller has no specific image in mind — usually the highest-
    quality/most-complete one). Falls back further to the denormalized
    item.preview_data only when there are no PreviewImage rows at all
    (no index concept there, since it's a single inline field).

    Returns (image_bytes | None, resolved_index | None) — resolved_index
    is the index actually used (echoed back to the client so it's never
    ambiguous which image a suggestion result is based on), or None when
    there were no PreviewImage rows to index into.
    """
    imgs = list(item.preview_images.order_by('order'))
    if imgs:
        if image_index is not None and 0 <= image_index < len(imgs):
            return bytes(imgs[image_index].data), image_index
        best_idx = max(range(len(imgs)), key=lambda i: len(imgs[i].data or b''))
        return bytes(imgs[best_idx].data), best_idx
    if item.preview_data:
        return bytes(item.preview_data), None
    return None, None


def _remove_preview_images(item, ids):
    """Delete `ids` (PreviewImage ids of `item`) and keep everything that
    refers to an image's POSITION consistent.

    Remaining images are renumbered contiguously (the order the previews list
    and ?index=N use). Item.character_regions boxes name their image by that
    same index, so after a delete they must follow their image to its new
    index, and the boxes drawn on a deleted image must go -- otherwise a box
    silently ends up on a different picture and trains the classifier on the
    wrong crop. A region with image_index None means "the default image" (the
    largest, see _select_image_bytes): it survives only while that image does.

    Returns {'deleted', 'remaining', 'regions_dropped', 'regions_remapped'}.
    """
    from django.db import transaction
    from django.db.models.functions import Length

    ids = {int(i) for i in ids}
    with transaction.atomic():
        rows = list(item.preview_images.order_by('order').annotate(size=Length('data')).values('id', 'size'))
        doomed = [pos for pos, row in enumerate(rows) if row['id'] in ids]
        if not doomed:
            return {'deleted': 0, 'remaining': len(rows), 'regions_dropped': 0, 'regions_remapped': 0}
        doomed_set = set(doomed)
        survivors = [pos for pos in range(len(rows)) if pos not in doomed_set]
        new_index = {old: new for new, old in enumerate(survivors)}
        default_pos = max(range(len(rows)), key=lambda pos: rows[pos]['size'] or 0)

        PreviewImage.objects.filter(item=item, id__in=[rows[pos]['id'] for pos in doomed]).delete()
        for old, new in new_index.items():
            if old != new:
                PreviewImage.objects.filter(pk=rows[old]['id']).update(order=new)

        dropped = remapped = 0
        regions = []
        for region in (item.character_regions or []):
            idx = region.get('image_index')
            if idx is None:
                if default_pos in doomed_set:
                    dropped += 1
                    continue
            elif idx in doomed_set:
                dropped += 1
                continue
            elif idx in new_index and new_index[idx] != idx:
                region = {**region, 'image_index': new_index[idx]}
                remapped += 1
            regions.append(region)
        if dropped or remapped:
            item.character_regions = regions
            item.save(update_fields=['character_regions'])
        if not survivors:
            item.preview_data = None
            item.preview_content_type = None
            item.save(update_fields=['preview_data', 'preview_content_type'])
    return {'deleted': len(doomed), 'remaining': len(survivors), 'regions_dropped': dropped, 'regions_remapped': remapped}


def _natural_sort_key(s):
    """Sort key for natural/numerical ordering (e.g. 05.jpg < 21.jpg, 2.jpg < 10.jpg)."""
    if not s:
        return []
    import re
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def _reorder_preview_images(item, new_ordered_ids):
    """Reorder item's PreviewImages to match `new_ordered_ids`.
    Also updates character_regions and translation_caches to follow the new positions.
    """
    from django.db import transaction

    with transaction.atomic():
        rows = list(item.preview_images.order_by('order'))
        if not rows:
            return {'count': 0, 'regions_remapped': 0}

        row_map = {r.id: r for r in rows}
        old_index_by_id = {r.id: idx for idx, r in enumerate(rows)}

        # Filter new_ordered_ids to valid ones belonging to this item
        valid_ids = [rid for rid in new_ordered_ids if rid in row_map]
        # Any missing rows keep their relative order at the end
        remaining = [r.id for r in rows if r.id not in set(valid_ids)]
        final_ids = valid_ids + remaining

        new_index_by_id = {rid: idx for idx, rid in enumerate(final_ids)}
        pos_map = {old_index_by_id[rid]: new_index_by_id[rid] for rid in final_ids}

        # Update PreviewImage order
        for rid, new_idx in new_index_by_id.items():
            if old_index_by_id[rid] != new_idx:
                PreviewImage.objects.filter(pk=rid).update(order=new_idx)

        # Update character_regions
        remapped_regions = 0
        if item.character_regions:
            new_regions = []
            for r in item.character_regions:
                idx = r.get('image_index')
                if idx is not None and idx in pos_map and pos_map[idx] != idx:
                    r = {**r, 'image_index': pos_map[idx]}
                    remapped_regions += 1
                new_regions.append(r)
            if remapped_regions > 0:
                item.character_regions = new_regions
                item.save(update_fields=['character_regions'])

        # Update translation_caches (unique_together = ('item', 'image_index'))
        caches = list(item.translation_caches.all())
        if caches:
            for c in caches:
                if c.image_index in pos_map and pos_map[c.image_index] != c.image_index:
                    c.image_index = 100000 + pos_map[c.image_index]
                    c.save(update_fields=['image_index'])
            for c in item.translation_caches.filter(image_index__gte=100000):
                c.image_index = c.image_index - 100000
                c.save(update_fields=['image_index'])

        return {'count': len(final_ids), 'regions_remapped': remapped_regions}


# How many of a post's images the suggestion pipeline analyses when the
# caller didn't pick one. Each image costs a full tagger pass (+ person crops,
# + CCIP), several to ~10 s on CPU, so a long post (a manga chapter) is
# sampled rather than walked page by page.
DEFAULT_MAX_IMAGES = 6
MAX_IMAGES_LIMIT = 12


def _select_images(item, image_index=None, max_images=DEFAULT_MAX_IMAGES):
    """[(index | None, image_bytes), ...] -- the images of `item` to analyse.

    An explicit `image_index` (the user picked one in the carousel) means
    exactly that image. Otherwise EVERY image of the post, up to
    `max_images`: a post often shows the same character in a back view, a
    close-up and a chibi, and judging from one of them (what this used to do:
    the largest file) throws the rest away. When there are more images than
    the cap they are sampled evenly, first and last included, so a long post
    is still covered end to end. Falls back to the legacy single preview
    blob when there are no PreviewImage rows (index None)."""
    imgs = list(item.preview_images.order_by('order'))
    if imgs:
        if image_index is not None and 0 <= image_index < len(imgs):
            return [(image_index, bytes(imgs[image_index].data))]
        if len(imgs) > max_images > 1:
            picks = sorted({round(k * (len(imgs) - 1) / (max_images - 1)) for k in range(max_images)})
        else:
            picks = list(range(min(len(imgs), max(1, max_images))))
        return [(i, bytes(imgs[i].data)) for i in picks if imgs[i].data]
    if item.preview_data:
        return [(None, bytes(item.preview_data))]
    return []


def _aggregate_confidences(per_image):
    """per_image: [{name: confidence}, ...] one dict per analysed image ->
    {name: (confidence, images_with, images_total, [image positions])}.

    The combined confidence is half the best single image and half the mean
    over ALL images (0 where absent): one strong sighting keeps a candidate
    alive (a post can show a different character on each page, so it must
    not be averaged away), while agreement across images ranks it higher
    (the same character on 3 of 4 images beats a single 4-image blip). With
    one image both halves are equal, so the value is unchanged."""
    n = len(per_image)
    out = {}
    for name in set().union(*per_image) if per_image else ():
        vals = [d.get(name, 0.0) for d in per_image]
        seen = [i for i, v in enumerate(vals) if v > 0]
        out[name] = (0.5 * max(vals) + 0.5 * sum(vals) / n, len(seen), n, seen)
    return out


def _aggregate_situation(hints):
    """One situation from per-image hints: content warning first (any R18
    image makes the post R18), then 'more than one person' (any), then SOLO
    only if every image that said anything said SOLO."""
    hints = [h for h in hints if h]
    for wanted in ('R18', 'MULTIPLE'):
        if wanted in hints:
            return wanted
    return hints[0] if hints and all(h == hints[0] for h in hints) else None


def _is_field_missing(item, field):
    """Mirrors ItemQueueManager.jsx's own `isFieldMissing` exactly — kept as
    a small module-level helper (rather than inlined Q() logic, like
    `incomplete`'s queryset filter) since `needs_attention_queue` needs it
    per-already-fetched-item, not as a DB-level filter.
    """
    if field in ('situation', 'artist'):
        return not getattr(item, field)
    v = getattr(item, field)
    return not isinstance(v, list) or len(v) == 0


def _char_diff_signature(region_chars, item_chars):
    """Content fingerprint of a (region-derived characters, item.characters)
    pair — see Item.character_regions_ack_signature's own docstring for why
    this is content-addressed rather than a plain boolean/timestamp: it
    naturally stops matching (so region_mismatch_queue re-flags the item)
    the instant either side actually changes again, with no invalidation
    bookkeeping needed anywhere characters/character_regions get written.
    """
    payload = json.dumps([sorted(region_chars), sorted(item_chars)], ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _expand_character_alias(char_name):
    """If `char_name` is a member of a confirmed CharacterAliasGroup (a
    human decided these names are the SAME identity — e.g. a magical
    girl's real name + transformed name — see that model's own docstring),
    return ALL of that group's names so a single tagger.predict_character
    call surfaces the complete set instead of just whichever name happened
    to be the training label (train_character_classifier._get_manual_
    labeled_rows picks one canonical name per linked group to train on).
    Otherwise returns [char_name] unchanged — the common case, and the
    only outcome for anyone who hasn't set up any alias groups at all.
    """
    # JSONField `contains` is Postgres/MySQL-only (SQLite raises
    # NotSupportedError — see backend.settings' DB_ENGINE toggle), so this
    # checks membership in Python instead. CharacterAliasGroup is a tiny
    # reference table (see its own model docstring), so scanning every row
    # here costs nothing.
    for group in CharacterAliasGroup.objects.filter(linked=True):
        if char_name in group.characters:
            return list(group.characters)
    return [char_name]


def _classifier_predictions(tagger_result, tagger_backend, k=3, floor=0.02):
    """[(character, probability), ...] best-first from the supplementary
    classifier (see tagger.predict_character_topk): its top `k` classes for
    each input, merged by name keeping the best probability.

    The classifier is trained on single-subject inputs: solo images, and
    manually region-labeled person crops. So it gets the same kind of input
    at inference: the whole image when 0-1 people were detected, and each
    detected person's OWN crop (tagger.suggest_tags's `crops`) when there
    are 2+. Feeding it a group shot's blended whole-image vector -- which
    used to be skipped entirely -- would be a train/inference mismatch, and
    skipping group shots entirely meant the region labels it was trained on
    could never help with exactly the images they came from. When 2+ people
    were detected but no crop could be processed, nothing is predicted.
    """
    person_count = tagger_result.get('person_count', 0)
    crops = tagger_result.get('crops') or []
    if person_count >= 2:
        inputs = [(c['general_probs'], c.get('embedding'), c.get('ccip')) for c in crops]
    else:
        inputs = [(tagger_result['general_probs'], tagger_result.get('embedding'), tagger_result.get('ccip'))]
    best = {}
    for probs, embedding, ccip in inputs:
        for name, p in tagger.predict_character_topk(probs, tagger_backend, k=k, floor=floor,
                                                      embedding=embedding, ccip=ccip):
            if p > best.get(name, 0.0):
                best[name] = p
    return sorted(best.items(), key=lambda kv: -kv[1])


def _classifier_needs(tagger_backend):
    """(with_embedding, with_ccip) for tagger.suggest_tags -- only the
    features the trained classifier actually reads are computed."""
    parts = tagger.feature_parts(tagger.classifier_feature_source(tagger_backend))
    return 'embedding' in parts, 'ccip' in parts


def _predict_crop_character_scores(crop, tagger_backend):
    """Predict character name -> score mapping for a single person crop."""
    scores = {}
    raw_chars = crop.get('characters') or []
    if raw_chars:
        matched, _ = _match_tagger_characters(raw_chars)
        for mc in matched:
            if mc.get('matched'):
                scores[mc['name']] = max(scores.get(mc['name'], 0.0), (mc.get('score') or 0.8) * 1.5)

    probs = crop.get('general_probs')
    if probs is not None:
        try:
            preds = tagger.predict_character_topk(
                probs, tagger_backend, k=5, floor=0.01,
                embedding=crop.get('embedding'), ccip=crop.get('ccip')
            )
            for name, p in preds:
                for alias in _expand_character_alias(name):
                    scores[alias] = max(scores.get(alias, 0.0), p * 2.0)
        except Exception:
            pass
    return scores


def _derive_proposed_regions(results, candidate_characters, tagger_backend, image_indices=None):
    """Derive region bounding boxes with candidate character assignments across analysed images.

    results: list of (position, tagger_result)
    candidate_characters: list of candidate character names (str) or candidate dicts with 'name'
    image_indices: list of original image index per position in results (optional)
    """
    proposed = []
    char_names = []
    for c in candidate_characters:
        if isinstance(c, dict):
            name = c.get('name')
        elif isinstance(c, str):
            name = c
        else:
            name = None
        if name and name not in char_names:
            char_names.append(name)

    for position, r in results:
        img_idx = image_indices[position] if image_indices and position < len(image_indices) else None
        crops = r.get('crops') or []
        boxes = r.get('boxes') or [c['box'] for c in crops]

        if not boxes:
            continue

        box_infos = []
        if crops and len(crops) == len(boxes):
            for crop in crops:
                scores = _predict_crop_character_scores(crop, tagger_backend)
                box_infos.append((crop['box'], scores))
        elif crops:
            for crop in crops:
                scores = _predict_crop_character_scores(crop, tagger_backend)
                box_infos.append((crop['box'], scores))
        else:
            # Fallback when person crops are not available (e.g. 1 box)
            whole_scores = {}
            probs = r.get('general_probs')
            if probs is not None:
                try:
                    preds = tagger.predict_character_topk(
                        probs, tagger_backend, k=5, floor=0.01,
                        embedding=r.get('embedding'), ccip=r.get('ccip')
                    )
                    for name, p in preds:
                        for alias in _expand_character_alias(name):
                            whole_scores[alias] = max(whole_scores.get(alias, 0.0), p * 2.0)
                except Exception:
                    pass
            for name in char_names:
                whole_scores.setdefault(name, 1.0)
            for box in boxes:
                box_infos.append((box, whole_scores))

        box_assignments = [None] * len(box_infos)

        if len(char_names) == 1:
            # Solo character manga/illustration: every detected person is that same character
            box_assignments = [char_names[0]] * len(box_infos)
        else:
            # Multi-character item: allow the same character to appear in multiple boxes
            # (e.g. manga with multiple panels containing the same character)
            for b_idx, (box, scores) in enumerate(box_infos):
                best_c = None
                best_s = 0.0
                for c_idx, c_name in enumerate(char_names):
                    s = scores.get(c_name, 0.0)
                    bonus = max(0.0, (len(char_names) - c_idx) * 0.001)
                    total_s = s + bonus
                    if total_s > best_s:
                        best_s = total_s
                        best_c = c_name
                # If score is confident (> 0.05), assign it regardless of other boxes
                if best_s > 0.05:
                    box_assignments[b_idx] = best_c

            # Fallback for unassigned boxes: if there are item characters not yet assigned
            # to any box, assign them to unassigned boxes in candidate order
            assigned_set = {c for c in box_assignments if c is not None}
            remaining = [c for c in char_names if c not in assigned_set]
            for b_idx in range(len(box_infos)):
                if box_assignments[b_idx] is None and remaining:
                    box_assignments[b_idx] = remaining.pop(0)

        for b_idx, (box, _scores) in enumerate(box_infos):
            assigned = box_assignments[b_idx]
            proposed.append({
                'image_index': img_idx,
                'box': [int(v) for v in box],
                'characters': [assigned] if assigned else [],
            })

    return proposed


def _suggest_for_item(item, external=False, tagger_backend='onnx',
                       general_threshold=0.35, character_threshold=0.85,
                       tag_limit_for_matching=None, use_classifier=False,
                       image_index=None):
    """Suggest titles/characters/tags/situation for `item`. Extracted from
    ItemViewSet.suggest_tags_view (the actual HTTP endpoint, now a thin
    wrapper around this) so it can also be called directly against an
    in-memory item — e.g. an unsaved clone with fields cleared — by
    item.management.commands.evaluate_full_pipeline, which needs to run the
    exact production suggestion logic offline without a live request.

    Fields the item ALREADY has filled in are left alone entirely — not
    even queried for — regardless of what DB history or the tagger might
    otherwise offer for them. "Already filled" uses the exact same
    emptiness check as the `incomplete` action's missing-field filter, so a
    field this item isn't missing never gets touched here.

    Among the fields actually wanted: the primary source is this item's
    artist's OTHER already-tagged items — a pure DB lookup (see
    _suggest_from_existing_data), near-instant, no image analysis. The
    image tagger only runs as a fallback for whichever wanted fields DB
    history didn't cover, since it's a ~5s+ CPU-bound operation per image
    and the DB signal, when available, is usually both faster and more
    precise (it's drawn from what this user already curated, not a model's
    guess).

    `use_classifier`: test-only hook, never set by suggest_tags_view
    (always False there — the live endpoint doesn't consult the
    supplementary classifier at all yet). When True, and the tagger's own
    direct character recognition didn't resolve `characters`, tries
    item.tagger.predict_character (see train_character_classifier) right
    alongside it, at the same priority tier — before falling through to
    tag-similarity attempt 2. Exists so evaluate_full_pipeline (the
    cascade) and evaluate_ensemble (the weighted combiner) can be compared
    with the SAME set of signals available to both, isolating "which
    combination strategy wins" from "does the classifier help at all" —
    see the classifier-architecture-comparison discussion for why those
    two questions need to stay separate.

    `tag_limit_for_matching`: test-only hook, never set by
    suggest_tags_view (always None there = current, unbounded production
    behavior). Truncates the tagger's own uncapped tag list to this many
    entries before it's used for DB tag-similarity matching (see Priority 3
    attempt 2 below) — lets evaluate_full_pipeline reproduce the "what if
    the tagger's own tag count were limited to N" axis from
    evaluate_tag_count, but against the full pipeline instead of the
    tag-similarity matcher in isolation. general_threshold/general_limit
    passed to the tagger itself are unrelated to this — general_limit only
    ever bounds what's shown to the user as suggested `tags`, never what
    feeds matching, in production or here.

    Nothing here is written to the Item — saving still goes through
    update_fields as normal.
    """
    want_titles = not (item.titles or [])
    want_characters = not (item.characters or [])
    want_tags = not (item.tags or [])
    want_situation = not item.situation

    titles, characters, tags, situation_hint = [], [], [], None
    title_candidates = []  # low-confidence fallback — see _suggest_from_similar_tags's docstring
    source = 'none'
    resolved_image_index = None

    def _remaining():
        return (want_titles and not titles) or (want_characters and not characters) or (want_situation and not situation_hint)

    # Priority 1: hashtags straight from the source post's own text
    # (Item.description) — the artist's own words, not an inference.
    # Tried before anything else for exactly that reason. Counts as
    # 'db' in the `source` field returned below (same bucket as the
    # other no-image-analysis DB lookups — the frontend only
    # distinguishes "used the image model" from "didn't").
    if want_titles or want_characters:
        hashtag_hits = _match_hashtags(item.description, external=external)
        if want_titles and hashtag_hits['titles']:
            titles = _merge_unique(titles, hashtag_hits['titles'])
            source = 'db'
        if want_characters and hashtag_hits['characters']:
            characters = [{'name': c, 'score': None, 'matched': True, 'source': 'hashtag'} for c in hashtag_hits['characters']]
            source = 'db'

    # Priority 2: this item's artist's OTHER already-tagged items — a
    # pure DB lookup, near-instant, no image analysis (most artists
    # repeatedly draw a small set of series/characters). Skipped
    # entirely if hashtags above already resolved everything wanted.
    db = _suggest_from_existing_data(item) if _remaining() else None
    if db:
        if want_titles and not titles:
            titles = list(db['titles'])
            title_candidates = _merge_unique(title_candidates, db['title_candidates'])
        if want_characters and not characters and db['characters']:
            characters = [{'name': c, 'score': None, 'matched': True, 'source': 'db'} for c in db['characters']]
        if want_situation and not situation_hint:
            situation_hint = db['situation_hint']
        if source == 'none' and (titles or characters):
            source = 'db'

    def _merge_tag_similarity_result(sim):
        """Folds a _suggest_from_similar_tags() result into
        titles/characters/situation_hint/title_candidates (only for
        fields still wanted and not already resolved above), and
        records whether it actually contributed anything.
        """
        nonlocal titles, characters, situation_hint, title_candidates, source
        contributed = False
        if want_titles:
            if sim['titles']:
                titles = _merge_unique(titles, sim['titles'])
                contributed = True
            elif not titles:
                title_candidates = _merge_unique(title_candidates, sim['title_candidates'])
        if want_characters and not characters and sim['characters']:
            characters = [{'name': c, 'score': None, 'matched': True, 'source': 'tag'} for c in sim['characters']]
            contributed = True
        if want_situation and not situation_hint and sim['situation_hint']:
            situation_hint = sim['situation_hint']
            contributed = True
        if contributed:
            if source == 'none':
                source = 'db'
            elif source == 'tagger':
                source = 'db+tagger'
            # already 'db' or 'db+tagger' — no change needed

    # Priority 3: tag-based similarity, attempt 1 — with whatever tags
    # this item already has (if any), before ever invoking the tagger.
    # Complements the artist-based prior above: it catches "different
    # artist, same character" cases that "this artist's other work"
    # can never see.
    if not want_tags and _remaining():
        _merge_tag_similarity_result(_suggest_from_similar_tags(item.tags or [], item.description, exclude_pk=item.pk))

    needs_tagger = (want_titles and not titles) or (want_characters and not characters) or want_tags
    if needs_tagger and HAVE_TAGGER:
        image_bytes, resolved_image_index = _select_image_bytes(item, image_index)

        if image_bytes is not None:
            try:
                tagger_result = tagger.suggest_tags(
                    image_bytes,
                    general_threshold=general_threshold,
                    character_threshold=character_threshold,
                    backend=tagger_backend,
                    **dict(zip(('with_embedding', 'with_ccip'), _classifier_needs(tagger_backend))),
                )
            except Exception:
                logging.exception('Tagger inference failed for item %s', item.id)
                tagger_result = None

            if tagger_result is not None:
                source = 'tagger' if source == 'none' else 'db+tagger'
                if want_characters and not characters:
                    matched_chars, tagger_titles = _match_tagger_characters(tagger_result['characters'])
                    # Only keep entries the tagger's output actually
                    # resolved to a real DB character — an unmatched entry
                    # is a raw, untranslated tagger string (e.g. "hakurei
                    # reimu") that has no business being auto-applied as a
                    # suggested character.
                    characters = [c for c in matched_chars if c.get('matched')]
                    if want_titles:
                        titles = _merge_unique(titles, tagger_titles)

                    # A character the tagger recognized but that matches
                    # nothing in this app's own vocabulary yet — treated
                    # as reliable (it's the model's own identification,
                    # not a guess) and reverse-looked-up against Danbooru
                    # to name a genuinely new title, rather than only
                    # ever being able to suggest titles already seen
                    # locally. Opt-in only (external flag) since this is
                    # the one network call in the whole pipeline.
                    if external and want_titles:
                        for c in matched_chars:
                            if titles:
                                break  # got an answer — no need to keep querying Danbooru
                            if c.get('matched'):
                                continue
                            looked_up = _resolve_title_from_character(c['name'])
                            if looked_up:
                                titles = _merge_unique(titles, [looked_up])
                                source = f'{source}+danbooru' if 'danbooru' not in source else source

                    # Same priority tier as the tagger's own direct
                    # recognition just above — only tried because that
                    # didn't resolve anything, and only trusted on
                    # single-subject images (see tagger.predict_character's
                    # docstring: it was never trained on multi-character
                    # crops, so a blended multi-person image would be a
                    # meaningless extrapolation for it).
                    if use_classifier and want_characters and not characters:
                        predictions = [
                            (n, p) for n, p in _classifier_predictions(tagger_result, tagger_backend, k=1, floor=0.5)
                        ]
                        if predictions:
                            char_name, confidence = predictions[0]
                            characters = [
                                {'name': n, 'score': confidence, 'matched': True, 'source': 'classifier'}
                                for n in _expand_character_alias(char_name)
                            ]
                            source = 'tagger' if source == 'none' else 'db+tagger'
                if want_tags:
                    tags = tagger_result['tags']
                if want_situation and not situation_hint:
                    situation_hint = tagger_result['situation_hint']

                # Tag-based similarity, attempt 2: now that this item has
                # freshly-inferred tags (uncapped — general_limit only
                # bounds what's shown as suggested `tags`, not what's used
                # here for matching), retry whatever's still unresolved.
                if _remaining():
                    tags_for_matching = tagger_result['tags_full']
                    if tag_limit_for_matching is not None:
                        tags_for_matching = tags_for_matching[:tag_limit_for_matching]
                    _merge_tag_similarity_result(_suggest_from_similar_tags(tags_for_matching, item.description, exclude_pk=item.pk))

    # Last resort: nothing above (hashtags, DB history, tag similarity,
    # tagger+CharacterGroup match, Danbooru reverse lookup) could name an
    # existing title for this item. Rather than leaving titles empty,
    # check the source post's own text for an explicit "this is an
    # original character" signal — purely local (no network call),
    # independent of the `external` flag.
    if want_titles and not titles and _looks_like_oc(item.description):
        titles = ['OC']

    if titles:
        title_candidates = []  # a confident answer supersedes the low-confidence list

    proposed_regions = []
    if tagger_result is not None:
        proposed_regions = _derive_proposed_regions(
            [(0, tagger_result)],
            characters,
            tagger_backend,
            image_indices=[resolved_image_index] if resolved_image_index is not None else None,
        )

    return {
        'characters': characters,
        'titles': titles,
        'tags': tags,
        'situation_hint': situation_hint,
        'suggested_titles': titles,
        'title_candidates': title_candidates,
        'proposed_regions': proposed_regions,
        'source': source,
        'sample_size': db['sample_size'] if db else 0,
        'image_index': resolved_image_index,
    }


def _append_tag_similarity_candidates(sim, title_c, char_c, situation_c,
                                       want_titles, want_characters, want_situation):
    """Shared by _collect_candidates's two tag-similarity calls (item's own
    tags, then the tagger's freshly-inferred tags) — folds one
    _suggest_from_similar_tags() result into the running candidate lists,
    tagging each entry with its source so _combine_candidates can weight
    it. Each field is still gated on its own want_* flag — the caller only
    checks whether ANY field is wanted before bothering to call
    _suggest_from_similar_tags at all (skip the query when nothing needs
    it), not which specific ones."""
    if want_titles:
        for t in sim['titles']:
            title_c.append({'value': t, 'source': 'tag_similarity', 'confidence': 1.0})
        for t in sim['title_candidates']:
            title_c.append({'value': t, 'source': 'tag_similarity_weak', 'confidence': 1.0})
    if want_characters:
        for c in sim['characters']:
            char_c.append({'value': c, 'source': 'tag_similarity', 'confidence': 1.0})
    if want_situation and sim['situation_hint']:
        situation_c.append({'value': sim['situation_hint'], 'source': 'tag_similarity', 'confidence': 1.0})


def _collect_candidates(item, external=False, tagger_backend='onnx',
                         general_threshold=0.35, character_threshold=0.85,
                         tag_limit_for_matching=None, image_index=None, max_images=DEFAULT_MAX_IMAGES):
    """Candidate-collecting counterpart to _suggest_for_item, used both by
    _suggest_for_item_ensemble (the live suggest_tags endpoint's default —
    see that function's own docstring) and directly by
    item.management.commands.evaluate_ensemble for offline evaluation.
    _suggest_for_item stops at the first source that
    resolves a field, on the reasoning that DB signals are generally more
    reliable than the image model's guess; in practice that means a weak
    DB signal (e.g. an artist's history that barely clears its own
    confidence bar) can block a stronger, more specific signal (e.g. the
    tagger directly recognizing a character in THIS image) from ever even
    running, since it never gets the chance to.

    This instead runs every applicable source unconditionally (still
    respecting want_titles/want_characters/want_tags/want_situation — a
    field the item already has filled in is still never touched) and
    returns ALL of their candidates, each tagged with which source
    proposed it and a per-candidate confidence (1.0 for sources that are
    internally already threshold-gated and don't expose a finer-grained
    score of their own; the tagger's own per-character sigmoid score where
    it has one). A downstream weighted combiner (_combine_candidates) then
    picks the best-supported answer per field instead of whichever source
    happened to run first.

    Returns {'title': [...], 'character': [...], 'situation': [...],
    'tags': [...]} — title/character/situation entries are
    {'value', 'source', 'confidence'} dicts; 'tags' is just the tagger's
    own display tag list (a single-source field — no ensemble needed since
    nothing else proposes tags).
    """
    want_titles = not (item.titles or [])
    want_characters = not (item.characters or [])
    want_tags = not (item.tags or [])
    want_situation = not item.situation

    title_c, char_c, situation_c, tags_out = [], [], [], []

    # The image is picked once, up front: whether it is black-and-white
    # changes which evidence is trustworthy for EVERY source below (see
    # _MONOCHROME_ENSEMBLE_WEIGHTS and _suggest_from_similar_tags'
    # `monochrome`), not just for the tagger.
    images, monochrome = [], False
    if want_titles or want_characters or want_tags or want_situation:
        images = _select_images(item, image_index, max_images)
        if images:
            # a mostly-black-and-white post (a manga with a colour cover)
            # counts as monochrome: the colour cues are missing from most of it
            monochrome = sum(tagger.is_monochrome(b) for _i, b in images) * 2 > len(images)
    image_indices = [i for i, _b in images]
    resolved_image_index = image_indices[0] if len(images) == 1 else None

    if want_titles or want_characters:
        hashtag_hits = _match_hashtags(item.description, external=external)
        if want_titles:
            for t in hashtag_hits['titles']:
                title_c.append({'value': t, 'source': 'hashtag', 'confidence': 1.0})
        if want_characters:
            for c in hashtag_hits['characters']:
                char_c.append({'value': c, 'source': 'hashtag', 'confidence': 1.0})
        if want_titles:
            for t in hashtag_hits['titles_partial']:
                title_c.append({'value': t, 'source': 'hashtag_partial', 'confidence': 1.0})
        if want_characters:
            for c in hashtag_hits['characters_partial']:
                char_c.append({'value': c, 'source': 'hashtag_partial', 'confidence': 1.0})

    # Translate the post's own caption to English and search Danbooru for
    # a matching title/character (see danbooru_lookup.match_description_
    # to_danbooru) -- an external network call (translation + Danbooru
    # both), so gated behind the same `external` opt-in as every other
    # Danbooru-facing source below, not run unconditionally.
    if external and (want_titles or want_characters) and (item.description or '').strip():
        desc_match = danbooru_lookup.match_description_to_danbooru(item.description)
        if want_titles and desc_match['title_name']:
            title_c.append({'value': desc_match['title_name'], 'source': 'danbooru_description', 'confidence': 1.0})
        if want_characters:
            for c in desc_match['character_names']:
                char_c.append({'value': c, 'source': 'danbooru_description', 'confidence': 1.0})

    if want_titles or want_characters or want_situation:
        db = _suggest_from_existing_data(item)
        if db:
            if want_titles:
                for t in db['titles']:
                    title_c.append({'value': t, 'source': 'artist_history', 'confidence': 1.0})
                for t in db['title_candidates']:
                    title_c.append({'value': t, 'source': 'artist_history_weak', 'confidence': 1.0})
            if want_characters:
                for c in db['characters']:
                    char_c.append({'value': c, 'source': 'artist_history', 'confidence': 1.0})
            if want_situation and db['situation_hint']:
                situation_c.append({'value': db['situation_hint'], 'source': 'artist_history', 'confidence': 1.0})

    if not want_tags and (want_titles or want_characters or want_situation):
        sim = _suggest_from_similar_tags(item.tags or [], item.description, exclude_pk=item.pk, monochrome=monochrome)
        _append_tag_similarity_candidates(sim, title_c, char_c, situation_c,
                                           want_titles, want_characters, want_situation)

    # One tagger pass per analysed image; the results are then combined (see
    # _aggregate_confidences). Each image keeps its own person detection and
    # per-person crops, so a post with a different character on every page
    # still yields every character, with "k of n images" as the evidence.
    results = []
    if (want_titles or want_characters or want_tags or want_situation) and HAVE_TAGGER:
        needs = dict(zip(('with_embedding', 'with_ccip'), _classifier_needs(tagger_backend)))
        for position, (index, image_bytes) in enumerate(images):
            try:
                results.append((position, tagger.suggest_tags(
                    image_bytes,
                    general_threshold=general_threshold,
                    character_threshold=character_threshold,
                    backend=tagger_backend,
                    **needs,
                )))
            except Exception:
                logging.exception('Tagger inference failed for item %s image %s', item.id, index)
    n_results = len(results)

    def support(entry_positions):
        return {'image_indices': [image_indices[results[p][0]] for p in entry_positions
                                  if image_indices[results[p][0]] is not None]}

    if results:
        if want_tags:
            merged_tags = {}
            for _p, r in results:
                for t in r['tags']:
                    merged_tags[t['name']] = max(merged_tags.get(t['name'], 0.0), t['score'])
            tags_out = [{'name': n, 'score': round(sc, 4)}
                        for n, sc in sorted(merged_tags.items(), key=lambda kv: -kv[1])[:15]]
        if want_situation:
            hint = _aggregate_situation([r['situation_hint'] for _p, r in results])
            if hint:
                situation_c.append({'value': hint, 'source': 'tagger', 'confidence': 1.0})

        if want_characters or want_titles:
            per_image_raw = [{c['name']: c['score'] for c in r['characters']} for _p, r in results]
            raw_agg = _aggregate_confidences(per_image_raw)
            raw_candidates = [{'name': n, 'score': round(v[0], 4)} for n, v in raw_agg.items()]
            matched_chars, tagger_titles = _match_tagger_characters(raw_candidates)
            if want_characters:
                # Only entries the tagger's output actually resolved to a
                # real DB character (matched=True) are legitimate candidate
                # VALUES here — an unmatched entry is still a raw, un-
                # translated tagger string (e.g. "hakurei reimu"), which
                # would otherwise compete in _combine_candidates as if it
                # were a real name and could end up suggested verbatim.
                for c in matched_chars:
                    if not c.get('matched'):
                        continue
                    confidence = c['score'] if c.get('score') is not None else 0.5
                    _conf, k, n, seen = raw_agg.get(c.get('raw_name'), (None, 1, n_results, []))
                    char_c.append({
                        'value': c['name'], 'source': 'tagger', 'confidence': confidence,
                        'raw_name': c.get('raw_name'), 'match_method': c.get('match_method'),
                        'images_with': k, 'images_total': n, **support(seen),
                    })
            if want_titles:
                for t in tagger_titles:
                    title_c.append({'value': t, 'source': 'tagger_group', 'confidence': 1.0})

            # Same reliable-model-output reasoning as _suggest_for_item's
            # Danbooru step — capped to the top 3 unmatched candidates by
            # score so this can't fire an unbounded number of network
            # calls per item (DanbooruTitleCache still dedupes repeats
            # across items on top of that).
            if external and want_titles:
                unmatched = sorted(
                    (c for c in matched_chars if not c.get('matched')),
                    key=lambda c: -(c['score'] or 0),
                )[:3]
                for c in unmatched:
                    looked_up = _resolve_title_from_character(c['name'])
                    if looked_up:
                        title_c.append({'value': looked_up, 'source': 'danbooru', 'confidence': 1.0})

            # Supplementary classifier trained on this app's own labeled
            # images (see item.management.commands.train_character_classifier)
            # — covers characters the Danbooru-trained tagger backends
            # structurally can't (OCs, titles not yet in Danbooru's tag
            # vocabulary). Run per person crop on a multi-person image (see
            # _classifier_predictions) -- it was trained on single-subject
            # inputs, so a group shot's blended whole-image features would
            # be a meaningless extrapolation for it -- and per analysed image.
            if want_characters:
                per_image_cls = [dict(_classifier_predictions(r, tagger_backend)) for _p, r in results]
                for char_name, (confidence, k, n, seen) in _aggregate_confidences(per_image_cls).items():
                    for alias in _expand_character_alias(char_name):
                        char_c.append({'value': alias, 'source': 'classifier', 'confidence': confidence,
                                       'images_with': k, 'images_total': n, **support(seen)})

        if want_titles or want_characters or want_situation:
            # Tags that most of the analysed images agree on: the union over
            # a post's images blends different characters' tags into one
            # incoherent set, which is exactly what tag similarity can't use.
            counts = Counter(t for _p, r in results for t in set(r['tags_full']))
            need = (n_results + 1) // 2
            tags_for_matching = [t for t, c in counts.most_common() if c >= need] or list(counts)
            if tag_limit_for_matching is not None:
                tags_for_matching = tags_for_matching[:tag_limit_for_matching]
            sim2 = _suggest_from_similar_tags(tags_for_matching, item.description, exclude_pk=item.pk, monochrome=monochrome)
            _append_tag_similarity_candidates(sim2, title_c, char_c, situation_c,
                                               want_titles, want_characters, want_situation)

    logging.info(
        'suggest item=%s backend=%s want(t/c/tag/s)=%s/%s/%s/%s have_tagger=%s '
        'image_index=%s tagger_ran=%s cand(title/char/situation/tags)=%d/%d/%d/%d',
        item.id, tagger_backend, want_titles, want_characters, want_tags, want_situation,
        HAVE_TAGGER, image_indices, n_results > 0,
        len(title_c), len(char_c), len(situation_c), len(tags_out),
    )
    return {
        'title': title_c, 'character': char_c, 'situation': situation_c, 'tags': tags_out,
        'want_titles': want_titles, 'want_characters': want_characters,
        'want_tags': want_tags, 'want_situation': want_situation,
        'image_index': resolved_image_index, 'image_indices': image_indices, 'images_analyzed': n_results,
        'monochrome': monochrome,
        'results': results, 'tagger_backend': tagger_backend,
    }


# Starting weights, hand-tuned by rough source reliability (hashtags are the
# artist's own words; Danbooru reverse lookup is the model's own recognition
# corroborated by an authoritative external source; DB/tag-similarity fall
# in between) — meant to be swept/tuned against real confirmed-DB accuracy
# (see evaluate_ensemble --grid-search), not treated as final.
DEFAULT_ENSEMBLE_WEIGHTS = {
    # Untested by the grid search below (see hashtag's own note) or
    # untestable by this offline methodology at all (danbooru — see
    # evaluate_ensemble's own docstring on why) — kept at their original,
    # reasoning-based values rather than whatever the grid happened to show.
    'hashtag': 5.0,
    # A hashtag that merely CONTAINS a registered name (or vice versa) --
    # see _is_partial_match. Not grid-searched; sits between the
    # single-source image signals and an exact hashtag hit.
    'hashtag_partial': 2.0,
    'danbooru': 3.0,
    # Empirically tuned: a 6-dimension grid search (evaluate_ensemble
    # --grid-search) against confirmed real DB items, cross-checked across
    # a 10-seed sweep (item.views._suggest_for_item's use_classifier flag
    # vs this combiner, same weights, same items) — the ensemble beat the
    # old single-source-wins cascade in 10/10 sampled seeds on character
    # accuracy (avg 62.6% vs 51.0%). 'classifier' consistently topped out
    # at the grid's max tested value in every winning combination, which is
    # why it's now the single highest weight here.
    'artist_history': 2.0,
    'artist_history_weak': 1.0,
    'tag_similarity': 1.0,
    'tag_similarity_weak': 0.5,
    'tagger': 2.0,
    'tagger_group': 2.0,
    'classifier': 4.0,
    # Cross-field boosts (see _cross_boost_title_candidates/_cross_boost_
    # character_candidates below) -- a second-order inference derived from
    # the OTHER field's own already-uncertain combined score, so weighted
    # more modestly than a direct signal like hashtag/danbooru/artist_
    # history; not grid-searched (the existing grid search only ever
    # optimized character accuracy against the pre-cross-boost candidate
    # pool), just a reasoned starting point in the same spirit as hashtag/
    # danbooru's own untested-by-the-grid weights above.
    'character_group': 1.5,
    'title_group': 1.5,
    # Translated-description -> Danbooru search -> link-table reverse
    # lookup (see danbooru_lookup.match_description_to_danbooru) — weighted
    # the same as 'danbooru' (a comparable "external Danbooru confirmation,
    # then resolved through an already-reviewed link table" signal), even
    # though it goes through more steps (translation quality, a proper-
    # noun-phrase heuristic, fuzzy autocomplete matching) than 'danbooru'
    # itself does — every one of those steps still has to land on a REAL
    # Danbooru tag with an existing link row before this contributes
    # anything at all, so a wrong guess doesn't silently sneak through as
    # if it were as reliable as a hashtag.
    'danbooru_description': 3.0,
}

# A same-request character/title combined score is capped at this before
# being reused as another field's synthetic candidate `confidence` --
# combined scores are unbounded weighted sums (a single hashtag hit alone
# is already 5.0), so without a cap an already-strong candidate on one
# field would swamp DEFAULT_ENSEMBLE_WEIGHTS' scale on the other field
# entirely out of proportion to every other source, which is scaled
# against confidences in roughly the 0-1 range (a tagger's own sigmoid
# score, or a flat 1.0 for hashtag/danbooru/artist_history).
_CROSS_FIELD_CONFIDENCE_CAP = 1.0
# Only a candidate whose OWN combined score already clears this bar gets
# to vote on the other field at all -- roughly "at least one full-
# confidence source, or a couple of weaker ones agreeing", so a single
# barely-above-zero guess (e.g. one tag_similarity_weak hit alone, ~0.15)
# doesn't get to introduce noise into the other field.
_CROSS_FIELD_MIN_SCORE = 1.0

# Weights when the selected image is black-and-white (tagger.is_monochrome).
# Colour is the cue the tag-based sources lean on hardest -- eye/hair colour
# tags are what tag similarity matches on, and what the tagger's character
# recognition and the tag-feature classifier see most clearly -- so on a
# monochrome image they are down-weighted, and the sources that never looked
# at the image (the artist's own hashtags and history) keep or gain weight.
# Measured: the tag-feature classifier is ~10 points less accurate on
# monochrome crops than on colour ones (73% vs 82%, item-grouped CV). The
# specific multipliers below are reasoned, NOT tuned -- there is no
# monochrome-only evaluation set large enough to grid-search them yet.
_MONOCHROME_WEIGHT_SCALE = {
    'tag_similarity': 0.5,
    'tag_similarity_weak': 0.5,
    'tagger': 0.75,
    'tagger_group': 0.75,
    'artist_history': 1.25,
    'hashtag_partial': 1.25,
}


def _ensemble_weights(monochrome):
    if not monochrome:
        return DEFAULT_ENSEMBLE_WEIGHTS
    return {src: w * _MONOCHROME_WEIGHT_SCALE.get(src, 1.0) for src, w in DEFAULT_ENSEMBLE_WEIGHTS.items()}


# Situation gets its OWN weight scheme, not DEFAULT_ENSEMBLE_WEIGHTS — the
# tagger's own composition/rating heuristic (see tagger._situation_hint:
# R18 from `rating` takes priority, then a Danbooru people-count tag like
# "2girls"/"multiple_girls" -> MULTIPLE, then 1girl+solo -> SOLO) is a
# direct read of the image itself and
# is considered reliable enough on its own that it isn't worth blending
# with the DB-derived priors the way title/character are — those exist to
# cover for the tagger being unavailable/undecided (no image, or a
# genuinely ambiguous 2-person composition tagger.py deliberately leaves
# unmapped), not to outvote it when it HAS an answer.
DEFAULT_SITUATION_WEIGHTS = {
    'tagger': 10.0,
    'artist_history': 1.0,
    'tag_similarity': 1.0,
}


def _combine_candidates(entries, weights, min_score=0.0, top_k=1):
    """entries: [{'value','source','confidence'}, ...] (one field's worth,
    from _collect_candidates). Sums weights[source] * confidence across
    every entry proposing the same value — so multiple sources agreeing on
    the same answer reinforces it — then returns the top_k values whose
    combined score clears min_score, highest first.

    Returns (values, scores) where `values` is a list (possibly empty) and
    `scores` is {value: combined_score} for every candidate considered
    (including ones that didn't clear min_score or make the top_k cut —
    useful for debugging/inspection).
    """
    scores = {}
    for e in entries:
        w = weights.get(e['source'], 0.0)
        if w == 0.0:
            continue
        scores[e['value']] = scores.get(e['value'], 0.0) + w * e['confidence']
    ranked = sorted((v for v in scores.items() if v[1] >= min_score), key=lambda kv: -kv[1])
    return [v for v, _s in ranked[:top_k]], scores


def _character_breakdown(entries, values):
    """For each of `values` (already-selected character candidates), the
    individual (source, confidence, raw_name, match_method) entries that
    contributed to its combined score — lets a human see WHICH signal(s)
    proposed a given name, and for a tagger-sourced one, whether it needed
    the Danbooru link bridge to get there at all (raw_name/match_method —
    see _match_tagger_characters). This is the difference between "the
    tagger itself misidentified the character" and "the tagger was right
    but the link table doesn't know that tag yet" when troubleshooting a
    wrong suggestion — the image/tagger/link split this exists for.
    """
    breakdown = {v: [] for v in values}
    for e in entries:
        if e['value'] in breakdown:
            breakdown[e['value']].append({
                'source': e['source'],
                'confidence': round(e['confidence'], 4),
                'raw_name': e.get('raw_name'),
                'match_method': e.get('match_method'),
                # multi-image evidence: seen on `images_with` of `images_total` analysed images
                'images_with': e.get('images_with'),
                'images_total': e.get('images_total'),
                'image_indices': e.get('image_indices'),
            })
    return breakdown


def _known_titles():
    """Every distinct title string used anywhere in the DB -- see
    _titles_for_group's own use of this (the group.name exact-match
    fallback), factored out so both directions of the title<->character
    cross-boost can share one query instead of each recomputing it."""
    known_titles = set()
    for titles in Item.objects.exclude(titles=[]).exclude(titles__isnull=True).values_list('titles', flat=True):
        known_titles.update(titles or [])
    return known_titles


def _titles_for_group(group, known_titles):
    """Same walk-up-the-parent-chain logic as _match_tagger_characters'
    own internal `titles_for_group` closure (see that function's
    docstring for the full reasoning on the group.name exact-match
    fallback and why a franchise umbrella like "Fate" is deliberately
    NOT treated as a title this way) -- kept as a separate, module-level
    copy rather than extracting/sharing that closure, since the two
    call sites build their own different supporting indexes already and
    aren't otherwise coupled."""
    node, seen = group, set()
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.titles:
            return node.titles
        if node.name in known_titles:
            return [node.name]
        node = node.parent
    return []


def _character_group_index():
    """dict: normalized character name -> the CharacterGroup it belongs
    to (first one found, mirroring _match_tagger_characters' own
    group_by_char_norm) -- used by _cross_boost_title_candidates below."""
    index = {}
    for group in CharacterGroup.objects.all():
        for c in (group.characters or []):
            index.setdefault(_normalize_char_name(c), group)
    return index


def _cross_boost_title_candidates(char_scores, known_titles):
    """Character inference -> title inference: a character candidate
    with a high OWN combined score (see _combine_candidates) makes the
    title(s) its CharacterGroup belongs to more likely, on top of
    whatever the item's own hashtags/tags/artist-history already
    suggest. Returns synthetic 'character_group' entries in the same
    {'value','source','confidence'} shape _collect_candidates produces,
    ready to be appended to collected['title'] before combining."""
    group_index = _character_group_index()
    boosts = []
    for char_name, score in char_scores.items():
        if score < _CROSS_FIELD_MIN_SCORE:
            continue
        group = group_index.get(_normalize_char_name(char_name))
        if not group:
            continue
        confidence = min(score, _CROSS_FIELD_CONFIDENCE_CAP)
        for title in _titles_for_group(group, known_titles):
            boosts.append({'value': title, 'source': 'character_group', 'confidence': confidence})
    return boosts


def _cross_boost_character_candidates(title_scores, known_titles):
    """The mirror direction: a title candidate with a high OWN combined
    score makes every character belonging to a CharacterGroup under that
    title more likely. Called AFTER title_scores already includes
    _cross_boost_title_candidates' own contribution (see
    _suggest_for_item_ensemble's call order) -- so a character can, in
    one pass, end up indirectly reinforced by its own group's title
    signal without needing an iterative fixed-point solve: character
    scores feed titles once, then the (now character-aware) title scores
    feed characters once, and both stop there."""
    strong_titles = {t for t, s in title_scores.items() if s >= _CROSS_FIELD_MIN_SCORE}
    if not strong_titles:
        return []
    boosts = []
    for group in CharacterGroup.objects.all():
        matched_titles = strong_titles & set(_titles_for_group(group, known_titles))
        if not matched_titles:
            continue
        confidence = min(max(title_scores[t] for t in matched_titles), _CROSS_FIELD_CONFIDENCE_CAP)
        for char_name in (group.characters or []):
            boosts.append({'value': char_name, 'source': 'title_group', 'confidence': confidence})
    return boosts


# How many ranked character/title candidates the review list can hold. It used
# to be cut at 3, but a group shot or a multi-image post can name 4+ characters;
# the UI shows the top 3 and folds the rest behind a toggle (EditFields.jsx).
_CANDIDATE_LIST_LIMIT = 12


def _suggest_for_item_ensemble(item, external=False, tagger_backend='onnx',
                                general_threshold=0.35, character_threshold=0.85,
                                image_index=None, max_images=DEFAULT_MAX_IMAGES):
    """Weighted-ensemble counterpart to _suggest_for_item — same
    want_titles/want_characters/want_tags/want_situation contract and
    return shape (drop-in compatible with suggest_tags_view's response),
    but resolves each field by summing every applicable source's weighted
    confidence (_collect_candidates + _combine_candidates, DEFAULT_ENSEMBLE_
    WEIGHTS/DEFAULT_SITUATION_WEIGHTS) instead of stopping at the first
    source to answer — see _collect_candidates's own docstring for why the
    cascade's "first source wins" design has a real structural flaw (a weak
    DB signal can block a stronger, more specific one from ever running).

    Selected via suggest_tags_view's `use_ensemble` request flag — this is
    now the endpoint's default (`_suggest_for_item`'s cascade is the
    opt-out, via `use_ensemble: false`). A real 10-seed sweep against
    confirmed DB items (both paths given the
    SAME candidate pool, same tagger backend, same classifier) showed this
    winning on character accuracy in every sampled seed (avg 62.6% vs the
    cascade's 51.0%) — see the classifier-architecture-comparison
    discussion this was built from for the full methodology.
    """
    collected = _collect_candidates(
        item, external=external, tagger_backend=tagger_backend,
        general_threshold=general_threshold, character_threshold=character_threshold,
        image_index=image_index, max_images=max_images,
    )

    weights = _ensemble_weights(collected['monochrome'])

    # Title <-> character cross-boost: a character candidate's own combined
    # score makes its CharacterGroup's title(s) more likely, and (using
    # that now-boosted title score) a title candidate's own combined score
    # makes every character in a CharacterGroup under that title more
    # likely in turn — see _cross_boost_title_candidates/_cross_boost_
    # character_candidates. Requires both fields to actually be wanted;
    # skipped entirely otherwise (nothing to boost, or nothing to boost
    # FROM).
    cross_boost = collected['want_titles'] and collected['want_characters']
    known_titles = _known_titles() if cross_boost else None

    # Stage 1: characters combined from their own sources only — this
    # (not the final, title-boosted char_scores below) is what titles get
    # boosted FROM, so the boost reflects "how well does the image/tags/
    # history alone support this character", not a value already inflated
    # by the very title boost it's about to help produce.
    _char_values_stage1, char_scores_stage1 = _combine_candidates(
        collected['character'], weights, top_k=_CANDIDATE_LIST_LIMIT,
    )

    title_entries = collected['title']
    if cross_boost:
        title_entries = title_entries + _cross_boost_title_candidates(char_scores_stage1, known_titles)
    title_values, title_scores = _combine_candidates(title_entries, weights, top_k=_CANDIDATE_LIST_LIMIT)
    # top_k=3 (not more): this is a REVIEW list a human picks from (see
    # EditFields.jsx's candidate cards), not an auto-apply-everything list
    # — three ranked options is enough to catch "the right answer wasn't
    # #1" without turning the review UI into a wall of low-confidence
    # noise. Titles used to be dumped straight into `suggested_titles` and
    # auto-applied wholesale despite this very comment already describing
    # the intended review-card design — this finishes that: titles now get
    # the exact same {name, score, contributors} shape `characters` does,
    # via the same (source-name-agnostic) _character_breakdown helper, so
    # a title suggestion is never applied without a human choosing it, and
    # always shows which source proposed it (crucially including 'danbooru'
    # — a live reverse lookup against Danbooru's OWN tag database, so it can
    # already name a title that has never been used anywhere in this app
    # before; that capability existed in `collected['title']` all along, it
    # just never survived past this function).
    char_entries = collected['character']
    if cross_boost:
        char_entries = char_entries + _cross_boost_character_candidates(title_scores, known_titles)
    char_values, char_scores = _combine_candidates(char_entries, weights, top_k=_CANDIDATE_LIST_LIMIT)
    situation_values, _situation_scores = _combine_candidates(collected['situation'], DEFAULT_SITUATION_WEIGHTS, top_k=1)

    char_breakdown = _character_breakdown(char_entries, char_values)
    characters = [
        {
            'name': name, 'score': round(char_scores[name], 4), 'matched': True, 'source': 'ensemble',
            'contributors': char_breakdown[name],
        }
        for name in char_values
    ]

    title_breakdown = _character_breakdown(title_entries, title_values)
    titles = [
        {'name': name, 'score': round(title_scores[name], 4), 'contributors': title_breakdown[name]}
        for name in title_values
    ]

    # Same last-resort OC fallback _suggest_for_item uses — nothing else
    # proposed a title at all, but the post's own text reads like an OC
    # disclaimer. No real source found this, so it gets a synthetic one
    # (score 0) purely so the frontend's card UI — which always shows a
    # contributor/source for every candidate — has something to label it
    # with instead of an empty list.
    if collected['want_titles'] and not titles and _looks_like_oc(item.description):
        titles = [{'name': 'OC', 'score': 0.0, 'contributors': [
            {'source': 'oc_heuristic', 'confidence': 1.0, 'raw_name': None, 'match_method': None},
        ]}]

    proposed_regions = _derive_proposed_regions(
        collected.get('results', []),
        characters,
        tagger_backend,
        image_indices=collected.get('image_indices'),
    )

    return {
        'characters': characters,
        'titles': titles,
        'tags': collected['tags'],
        'situation_hint': situation_values[0] if situation_values else None,
        'suggested_titles': [],
        'title_candidates': [],
        'proposed_regions': proposed_regions,
        'source': 'ensemble',
        'sample_size': 0,
        'image_index': collected['image_index'],
        'image_indices': collected['image_indices'],
        'images_analyzed': collected['images_analyzed'],
    }


class ItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Item viewset exposing read-only item list/retrieve and minimal preview endpoints."""
    queryset = Item.objects.defer('preview_data').annotate(
        annotated_has_preview=Exists(PreviewImage.objects.filter(item=OuterRef('pk')))
    ).order_by('-id')
    serializer_class = ItemSerializer

    def list(self, request, *args, **kwargs):
        # Log incoming request headers and remote addr to help reproduce
        # browser-specific 500s (captures headers, path and remote address).
        try:
            logging.info(
                "ItemViewSet.list called; path=%s remote=%s headers=%s",
                request.path,
                request.META.get('REMOTE_ADDR'),
                dict(request.headers)
            )
            return super().list(request, *args, **kwargs)
        except Exception as e:
            # Log full traceback to help debugging 500s in development
            logging.exception('Unhandled exception in ItemViewSet.list')
            tb = traceback.format_exc()
            print(tb)
            return Response({'detail': 'Internal server error', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        item = self.get_object()
        imgs = list(item.preview_images.order_by('order'))
        idx_param = request.GET.get('index')
        if idx_param is not None:
            try:
                idx = int(idx_param)
            except Exception:
                return Response({'detail': 'invalid index'}, status=status.HTTP_400_BAD_REQUEST)
            if idx < 0 or idx >= len(imgs):
                return Response({'detail': 'index out of range'}, status=status.HTTP_404_NOT_FOUND)
            img = imgs[idx]
            return HttpResponse(img.data, content_type=img.content_type or 'application/octet-stream')

        if imgs:
            best = max(imgs, key=lambda x: len(x.data or b''))
            return HttpResponse(best.data, content_type=best.content_type or 'application/octet-stream')

        if item.preview_data:
            return HttpResponse(item.preview_data, content_type=item.preview_content_type or 'application/octet-stream')

        return Response({'detail': 'No preview'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def fetch_and_save_preview(self, request, pk=None):
        item = self.get_object()
        # allow client to override the URL (useful when item.link is not the direct media page)
        data = request.data if isinstance(request.data, dict) else {}
        target_url = data.get('url') or item.link
        preview_only = bool(data.get('preview_only'))

        if not target_url:
            return Response({'detail': 'No link available on item'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_url(target_url)
        except SSRFError as e:
            return Response({'detail': f'URL not allowed: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        # Use the module-level request-based fetch helper for deterministic
        # server-side HTTP fetches. See `_fetch_image_via_requests` defined
        # at module scope for details and responsibilities.
        _internal_fetch = _fetch_image_via_requests

        # Read client-selected fetch method early so we can honor it below
        force_method = data.get('force_method') if isinstance(data, dict) else None
        # Allow clients to request the stored Twitter API JSON for debugging
        # without requiring an env change: include when request data contains
        # `debug: true` or the query param `?debug=1`/`?debug=true` is present.
        debug_requested = False
        try:
            if isinstance(data, dict) and bool(data.get('debug')):
                debug_requested = True
        except Exception:
            debug_requested = False
        qd = request.query_params.get('debug') if hasattr(request, 'query_params') else None
        if not debug_requested and qd is not None and str(qd).lower() in ('1', 'true', 'yes'):
            debug_requested = True

        # Track which method produced the candidates for debugging/UI
        used_method = None
        # Post body text captured alongside media, when the fetcher supports
        # it (currently: twitter_gql, yt-dlp) — saved onto item.description.
        # Hashtags in here are the most reliable signal for title/character
        # suggestion (see _match_hashtags), more reliable than image inference
        # since they're the artist's own words.
        fetched_description = ''

        # If the target URL itself points to an image, try that first
        body, ctype = _internal_fetch(target_url, min_size=MIN_IMAGE_FETCH_BYTES)
        candidates = []
        if body and ctype:
            used_method = 'direct'
            candidates.append((target_url, body, ctype))
        else:
            # Fetch HTML and try to extract common image hints (og:image, twitter:image, img src)
            try:
                import requests
                r = requests.get(target_url, timeout=15, headers={'User-Agent': 'fanart-viewer-bot/1.0'})
                html = r.text or ''
            except Exception:
                html = ''

            # Use BeautifulSoup (if available) to walk the DOM and collect
            # candidate image URLs. We aim to find images under the
            # 'div.react-root -> main.main -> a -> img' pattern, but also
            # fall back to common selectors (article img, figure img, og: tags).
            hints = []
            try:
                if 'BeautifulSoup' in globals() and BeautifulSoup is not None:
                    soup = BeautifulSoup(html, 'html.parser')
                    # Open Graph / twitter meta images first
                    og = soup.find('meta', property='og:image')
                    if og and og.get('content'):
                        hints.append(og.get('content'))
                    tw = soup.find('meta', attrs={'name': 'twitter:image'})
                    if tw and tw.get('content'):
                        hints.append(tw.get('content'))

                    # Target the common react-root -> main -> a -> img chain
                    # Note: Twitter uses an element with id="react-root" so
                    # prefer locating by id (not class) to match actual pages.
                    root = soup.find(id='react-root')
                    mains = []
                    if root:
                        # search within the react-root subtree for anchor->img patterns
                        mains = [root]
                    if not mains:
                        mains = soup.find_all('main')
                    for mtag in mains:
                        for a in mtag.find_all('a'):
                            for im in a.find_all('img'):
                                src = im.get('src')
                                if src:
                                    hints.append(src)

                    # Generic fallbacks
                    for im in soup.find_all('img'):
                        s = im.get('src')
                        if s:
                            hints.append(s)
                    for fig in soup.find_all('figure'):
                        im = fig.find('img')
                        if im and im.get('src'):
                            hints.append(im.get('src'))
            except Exception:
                # parsing failed; fall back to regex below
                pass

            # If BeautifulSoup parsing didn't yield anything, fallback to regex
            if not hints:
                m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                if m:
                    hints.append(m.group(1))
                m = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                if m:
                    hints.append(m.group(1))
                m = re.search(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
                if m:
                    hints.append(m.group(1))
                m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
                if m:
                    hints.append(m.group(1))

            # Resolve relative URLs and attempt fetches for ALL hints (do not stop on first)
            seen = set()
            # collect candidate source mapping for debug/UI
            candidate_sources = {}
            for h in hints:
                if not h:
                    continue
                try:
                    cand_url = urljoin(target_url, h)
                    # Upgrade Twitter thumbnail URLs to large for better quality
                    if 'pbs.twimg.com' in cand_url:
                        cand_url = re.sub(r'(?<=[?&])name=(?:small|medium|thumb|360x360|240x240)', 'name=large', cand_url)
                    # Skip profile images — we want tweet media images only
                    if '/profile_images/' in cand_url:
                        continue
                    if cand_url in seen:
                        continue
                    seen.add(cand_url)
                    b, ct = _internal_fetch(cand_url, min_size=MIN_IMAGE_FETCH_BYTES)
                    if b and ct:
                        candidates.append((cand_url, b, ct))
                        used_method = 'html'
                        # record where this candidate came from
                        candidate_sources[cand_url] = 'html'
                except Exception:
                    continue

            # For Twitter/X targets, also call the unified twitter helper to
            # aggregate additional HTML-derived candidates (scrape/nitter).
            # This helps collect multi-photo tweets where the page-level
            # meta tags only expose a single image.
            try:
                if (('twitter.com' in target_url) or ('x.com' in target_url)):
                    tw_urls = fetch_twitter_media_urls_with_sources(target_url)
                    for (tw_url, src) in tw_urls:
                        # prefer non-api sources here (we're improving HTML path)
                        if src == 'api':
                            continue
                        if tw_url in seen:
                            continue
                        try:
                            b, ct = _internal_fetch(tw_url, min_size=MIN_IMAGE_FETCH_BYTES)
                            if b and ct:
                                candidates.append((tw_url, b, ct))
                                used_method = used_method or 'html'
                                candidate_sources[tw_url] = src or 'scrape'
                                seen.add(tw_url)
                        except Exception:
                            continue
            except Exception:
                # don't fail the whole request if the helper errors
                pass

            # gallery-dl fetch for Twitter/X sensitive images (primary auth method).
            # gallery-dl maintains active Twitter support and handles sensitive
            # content reliably via cookie auth.
            if not candidates and HAVE_GALLERYDL and _have_twitter_creds():
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        gdl_results, gdl_description = fetch_twitter_media_gallerydl(target_url)
                        for (img_bytes, mime) in gdl_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'gallerydl'
                        if gdl_description and not fetched_description:
                            fetched_description = gdl_description
                    except Exception:
                        logging.exception('gallery-dl fetch failed for %s', target_url)

            # GraphQL API fallback (browser-free, but more fragile than gallery-dl).
            if not candidates and HAVE_TWITTER_GQL and _have_twitter_creds():
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        gql_results, gql_description = fetch_twitter_media(target_url)
                        for (img_bytes, mime) in gql_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'twitter_gql'
                        if gql_description and not fetched_description:
                            fetched_description = gql_description
                    except TwitterAuthError as e:
                        logging.warning('Twitter GQL auth error for %s: %s', target_url, e)
                    except Exception:
                        logging.exception('Twitter GQL fetch failed for %s', target_url)

            # yt-dlp fallback (primarily video, last resort for images).
            if not candidates and HAVE_YTDLP and _have_twitter_creds():
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        ytdlp_results, ytdlp_description = fetch_twitter_media_ytdlp(target_url)
                        for (img_bytes, mime) in ytdlp_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'ytdlp'
                        if ytdlp_description and not fetched_description:
                            fetched_description = ytdlp_description
                    except Exception:
                        logging.exception('yt-dlp fetch failed for %s', target_url)

            # Post text top-up: gallery-dl/twitter_gql/yt-dlp above are the
            # only sources of `fetched_description`, but all three are
            # gated behind `if not candidates` — skipped entirely once the
            # plain HTML/og:image scrape earlier already found an image.
            # X still server-renders og:image for ordinary public tweets
            # (link-preview support), so for any NON-sensitive tweet this
            # meant description silently stayed empty forever, even though
            # images were fetched successfully — description capture ended
            # up depending on whether a tweet happened to be sensitive-
            # flagged, which is not what "did the fetch succeed" should
            # mean. Always try once more here, purely for the text (no
            # image re-download — see fetch_tweet_description), whenever
            # it's still missing.
            if (not fetched_description and not item.description and HAVE_TWITTER_GQL
                    and _have_twitter_creds()
                    and (('twitter.com' in target_url) or ('x.com' in target_url))):
                try:
                    fetched_description = fetch_tweet_description(target_url) or ''
                except TwitterAuthError as e:
                    logging.warning('Twitter GQL description top-up auth error for %s: %s', target_url, e)
                except Exception:
                    logging.exception('Twitter GQL description top-up failed for %s', target_url)

            # Pixiv: none of the image-fetching paths here return the post's
            # title/tags, so the same "always try once more, text only" idea
            # as the Twitter top-up above -- author-set tags are what
            # item.views._match_hashtags reads. Enrichment only: a failure
            # here must never fail the image fetch itself.
            if (not fetched_description and not item.description and HAVE_PIXIV_PLAYWRIGHT
                    and 'pixiv.net/' in target_url):
                pixiv_id_match = re.search(r'/artworks/(\d+)', target_url)
                if pixiv_id_match:
                    try:
                        from .pixiv_bookmarks_fetch import fetch_illust_description
                        fetched_description = fetch_illust_description(int(pixiv_id_match.group(1))) or ''
                    except Exception:
                        logging.exception('Pixiv description/tag top-up failed for %s', target_url)

            # Poipiku: dedicated fetcher that handles IllustItemThubExpand and
            # the ShowAppendFile AJAX endpoint.  Run for any poipiku.com URL,
            # regardless of whether HTML scraping found something, because the
            # generic scraper only picks up the first thumbnail at _640 size.
            if HAVE_POIPIKU and 'poipiku.com' in target_url:
                try:
                    logging.info('poipiku: invoking fetch_poipiku_media for %s (generic scrape so far: %d candidate(s))',
                                 target_url, len(candidates))
                    poipiku_results = fetch_poipiku_media(target_url)
                    logging.info('poipiku: fetch_poipiku_media returned %d image(s), sizes=%s',
                                 len(poipiku_results), [len(b) for b, _ in poipiku_results])
                    if poipiku_results:
                        candidates = []  # replace generic results with poipiku-specific ones
                        for (img_bytes, mime) in poipiku_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                            else:
                                logging.info('poipiku: dropping a %d-byte image (< MIN_IMAGE_FETCH_BYTES=%d)',
                                             len(img_bytes or b''), MIN_IMAGE_FETCH_BYTES)
                        if candidates:
                            used_method = 'poipiku'
                    logging.info('poipiku: after poipiku-specific handling, %d candidate(s) remain (used_method=%s)',
                                 len(candidates), used_method)
                except Exception:
                    logging.exception('Poipiku fetch failed for %s', target_url)

            # If the client explicitly requested API mode for twitter/x, prefer
            # the API-based candidates (override HTML hints when API returns results).
            if force_method == 'api' and (('twitter.com' in target_url) or ('x.com' in target_url)):
                # If the client explicitly requested API mode but the server has
                # no TW_BEARER configured, return a helpful error so the UI
                # can show a clear message rather than silently falling back.
                if not os.environ.get('TW_BEARER'):
                    return Response({'detail': 'TW_BEARER not configured on server; API fetch unavailable'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
                try:
                    api_candidates = []
                    candidate_sources = {}
                    tw_urls = fetch_twitter_media_urls_with_sources(target_url)
                    # prefer API-origin results only when user explicitly forced API
                    api_only = [u for (u, s) in tw_urls if s == 'api']
                    for tw_url in api_only:
                        try:
                            b, ct = _internal_fetch(tw_url)
                            if b and ct:
                                api_candidates.append((tw_url, b, ct))
                                candidate_sources[tw_url] = 'api'
                        except Exception:
                            continue
                    # If API returned usable candidates, use them; otherwise return clear error
                    if api_candidates:
                        candidates = api_candidates
                        used_method = 'api'
                    else:
                        # If API returned no usable candidates, check whether the
                        # API response indicates rate limiting (429). If so, try a
                        # safe fallback to HTML scraping/Nitter to recover media.
                        api_debug = None
                        try:
                            api_debug = get_last_api_response(target_url)
                        except Exception:
                            api_debug = None

                        # If rate-limited, attempt to gather non-API candidates
                        # (scrape / nitter) and use them as a fallback. This keeps
                        # the user workflow working when API limits are hit.
                        tried_fallback = False
                        if api_debug and isinstance(api_debug, dict) and api_debug.get('status') == 429:
                            tried_fallback = True
                            try:
                                tw_urls = fetch_twitter_media_urls_with_sources(target_url)
                                fallbacks = [u for (u, s) in tw_urls if s != 'api']
                                fallback_candidates = []
                                candidate_sources = {}
                                for tw_url in fallbacks:
                                    try:
                                        b, ct = _internal_fetch(tw_url)
                                        if b and ct:
                                            fallback_candidates.append((tw_url, b, ct))
                                            candidate_sources[tw_url] = 'scrape'
                                    except Exception:
                                        continue
                                if fallback_candidates:
                                    candidates = fallback_candidates
                                    used_method = 'api_rate_limited_fallback'
                                else:
                                    # no fallback results available
                                    pass
                            except Exception:
                                pass

                        # If we have candidates from fallback, continue. Otherwise
                        # return a 422 with the API response attached for debugging.
                        if candidates:
                            # continue on to preview/save path
                            pass
                        else:
                            body = {'detail': 'API fetch returned no media for this tweet'}
                            if api_debug is not None:
                                body['api_response'] = api_debug
                            if tried_fallback:
                                body['note'] = 'API rate-limited; attempted scrape fallback.'
                            return Response(body, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
                except Exception:
                    # if API helper fails, continue with existing candidates
                    pass
            else:
                # If no candidates from HTML scraping and the client requested an API
                # fallback, use the unified twitter helper which can return multiple
                # candidate URLs. We only attempt API-based methods for twitter/x domains.
                # Do not silently fallback to non-API candidates when client forced API.
                # If we reach here and candidates are empty, above returned a 422.
                pass

            # Allow explicit Playwright-based fetch when requested (guarded by env)
            if force_method == 'playwright':
                if not os.environ.get('HEADLESS_ALLOWED'):
                    return Response({'detail': 'Playwright/headless fetch is not allowed in this environment'}, status=status.HTTP_403_FORBIDDEN)
                if not (('twitter.com' in target_url) or ('x.com' in target_url)):
                    # still allow other targets if caller explicitly requests it, but normally we target twitter/x
                    pass
                # browser choice may be provided by client (chromium/firefox/webkit)
                browser_choice = None
                try:
                    browser_choice = data.get('browser') if isinstance(data, dict) else None
                except Exception:
                    browser_choice = None
                try:
                    pw_headless = not bool(data.get('no_headless')) if isinstance(data, dict) else True
                except Exception:
                    pw_headless = True
                # For Pixiv targets, prefer the Pixiv-specific Playwright helper
                # which performs a logged-in fetch and returns image bytes. This
                # avoids relying on raw HTTP requests to pixiv-hosted URLs which
                # often require referer/cookies and can return placeholders.
                pixiv_handled = False
                try:
                    if ('pixiv.net' in target_url or 'pximg.net' in target_url) and HAVE_PIXIV_PLAYWRIGHT:
                        try:
                            # remember how many candidates we had before running the helper
                            _before_len = len(candidates)
                            pix_res = fetch_images_with_playwright(target_url, headful=not pw_headless)
                            if isinstance(pix_res, dict):
                                pix_images = pix_res.get('images') or []
                            else:
                                pix_images = pix_res
                            for entry in pix_images:
                                try:
                                    if isinstance(entry, (list, tuple)) and len(entry) >= 4:
                                        _, body, ctype, cand_url = entry[0], entry[1], entry[2], entry[3]
                                    elif isinstance(entry, (list, tuple)) and len(entry) == 3:
                                        _, body, ctype = entry
                                        cand_url = None
                                    else:
                                        continue
                                    if not cand_url:
                                        continue
                                    # skip SVGs
                                    if ctype and ctype.lower().split(';',1)[0] == 'image/svg+xml':
                                        continue
                                    # Skip images at or below the minimum fetch size.
                                    try:
                                        if len(body or b'') < MIN_IMAGE_FETCH_BYTES:
                                            continue
                                    except Exception:
                                        pass
                                    candidates.append((cand_url, body, ctype.split(';',1)[0]))
                                    used_method = used_method or 'playwright-pixiv'
                                except Exception:
                                    continue
                            # mark pixiv_handled only when the helper actually added candidates
                            pixiv_handled = len(candidates) > _before_len
                        except Exception as e:
                            logging.exception('Pixiv Playwright helper failed')
                            pixiv_handled = False
                except Exception:
                    pixiv_handled = False

                # If the Pixiv helper returned nothing, fall back to the generic
                # rendered-media extraction (which returns URLs). We then attempt
                # to fetch those URLs, but note that direct requests to Pixiv
                # hosts may fail; the helper above is preferred when available.
                if not pixiv_handled:
                    try:
                        pw_urls = fetch_rendered_media(target_url, browser_name=(browser_choice or 'chromium'), headless=pw_headless)
                    except Exception as e:
                        return Response({'detail': 'Playwright fetch failed', 'error': str(e)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

                    # convert returned URLs into candidates by attempting to fetch them
                    for h in pw_urls or []:
                        try:
                            if h:
                                # When fetching Playwright-discovered URLs, skip very small
                                # assets (icons/thumbnails). Require at least 10KB.
                                b, ct = _internal_fetch(h, min_size=MIN_IMAGE_FETCH_BYTES)
                                if b and ct:
                                    candidates.append((h, b, ct))
                                    used_method = used_method or 'playwright'
                        except Exception:
                            continue

        if not candidates:
            return Response({'detail': 'No image candidates found or failed to fetch', 'hints': hints}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Save captured post text onto the item (never overwrite a description
        # the user already has, e.g. from a manual edit or an earlier fetch).
        if fetched_description and not item.description:
            item.description = fetched_description
            item.save(update_fields=['description'])

        # If Playwright was explicitly requested, enforce a global minimum
        # Enforce the minimum image size across all scraping methods so
        # small icons/thumbnails are never returned to the client.
        try:
            if force_method == 'playwright':
                filtered = []
                for (u, b, ct) in candidates:
                    try:
                        if b and len(b) >= MIN_IMAGE_FETCH_BYTES:
                            filtered.append((u, b, ct))
                    except Exception:
                        # if size check fails, conservatively keep the candidate
                        filtered.append((u, b, ct))
                candidates = filtered
        except Exception:
            # be robust: if filtering fails for any reason, continue with
            # the unfiltered candidates rather than aborting the request
            logging.exception('Playwright size filtering failed')

        # If preview_only is requested, return candidates (with data_uri) without persisting
        if preview_only:
            images = []
            # candidate_sources may or may not be present depending on fetch path
            candidate_sources = locals().get('candidate_sources', {}) or {}
            for idx, (u, b, ct) in enumerate(candidates):
                try:
                    data_uri = f"data:{ct};base64,{base64.b64encode(b).decode('ascii')}"
                except Exception:
                    data_uri = None
                img = {'index': idx, 'url': u, 'size': len(b) if b else 0, 'content_type': ct, 'data_uri': data_uri}
                src = candidate_sources.get(u)
                if src:
                    img['source'] = src
                images.append(img)
            resp = {'preview_only': True, 'images': images}
            if used_method:
                resp['method'] = used_method
            if fetched_description:
                resp['description'] = fetched_description
            # If API debugging is enabled, include the raw API JSON (if available)
            # include API debug output if requested either via env var or per-request
            if os.environ.get('TW_API_DEBUG') or debug_requested:
                try:
                    api_debug = get_last_api_response(target_url)
                    if api_debug is not None:
                        resp['api_response'] = api_debug
                except Exception:
                    # be robust: don't fail the whole request if debug retrieval errors
                    logging.exception('Failed to fetch last API response for debug')
            return Response(resp)

        # Persist ALL successful candidates as preview images (preserve order)
        PreviewImage.objects.filter(item=item).delete()
        saved = []
        for idx, (url_f, body, ctype) in enumerate(candidates):
            try:
                pi = PreviewImage.objects.create(item=item, order=idx, data=body, content_type=ctype)
                saved.append({'id': pi.id, 'index': idx, 'url': url_f, 'size': len(body) if body else 0, 'content_type': ctype})
            except Exception:
                # skip individual failures but continue saving others
                logging.exception('Failed to save preview candidate %s for item %s', url_f, item.id)
                continue

        if not saved:
            return Response({'detail': 'Failed to save any preview images'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'status': 'saved', 'count': len(saved), 'saved': saved})

    @csrf_exempt
    @action(detail=False, methods=['post'], url_path='bookmark_fetch')
    def bookmark_fetch(self, request):
        """Resolve an Item from a Twitter/X URL and reuse the normal fetch flow.

        This is the entry point a browser extension or other client-side bridge
        can call after the bookmark action is detected in the browser. It
        accepts the current page URL as `url` and saves the fetched bytes to
        the database.
        """
        data = request.data if isinstance(request.data, dict) else {}
        target_url = data.get('url') or request.query_params.get('url')
        if not target_url:
            return Response({'detail': 'No URL provided'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_url(target_url)
        except SSRFError as e:
            return Response({'detail': f'URL not allowed: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        item = _find_item_by_url(target_url)
        item_created = False

        if item:
            # Item exists — skip if preview is already stored to avoid redundant fetches.
            # If preview is missing (e.g. a previous fetch failed), fall through and retry.
            try:
                has_preview = item.preview_images.exists() or bool(item.preview_data)
            except Exception:
                has_preview = False
            if has_preview:
                return Response({'status': 'already_processed', 'item_id': item.id}, status=status.HTTP_200_OK)
        else:
            normalized = _normalize_lookup_url(target_url)
            external_id = None
            source = None

            # Twitter/X: extract tweet ID and username from /{username}/status/<id>
            artist = ''
            try:
                m = re.search(r'/status/(\d+)', normalized)
                if m:
                    external_id = int(m.group(1))
                    source = 'twitter_bookmark'
                    um = re.search(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/status/', normalized)
                    if um:
                        artist = um.group(1)
            except Exception:
                pass

            # Pixiv: extract illust ID from /artworks/<id>
            if external_id is None:
                try:
                    m = re.search(r'/artworks/(\d+)', normalized)
                    if m:
                        external_id = int(m.group(1))
                        source = 'pixiv_bookmark'
                except Exception:
                    pass

            # Poipiku: extract illust ID from /{user_id}/{illust_id}.html
            if external_id is None:
                try:
                    m = re.search(r'poipiku\.com/\d+/(\d+)(?:\.html)?', normalized)
                    if m:
                        external_id = int(m.group(1))
                        source = 'poipiku_bookmark'
                except Exception:
                    pass

            if external_id is None:
                return Response({'detail': 'No matching item found for URL', 'url': target_url}, status=status.HTTP_404_NOT_FOUND)

            item = Item.objects.create(
                external_id=external_id,
                source=source,
                situation='',
                titles=[],
                characters=[],
                artist=artist,
                link=normalized,
                tags=None,
            )
            item_created = True

        # Run the expensive preview fetch/save flow after returning the HTTP response
        # so browser-side callers do not sit in a long pending state.
        try:
            threading.Thread(
                target=_run_bookmark_fetch_job,
                args=(item.pk, target_url, {'url': target_url}),
                daemon=True,
            ).start()
        except Exception:
            logging.exception('Failed to start background bookmark fetch job for item %s', item.pk)
            return Response({'detail': 'Failed to start background job'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                'status': 'processing',
                'item_id': item.id,
                'item_created': item_created,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'], url_path='fetch_account_retweets')
    def fetch_account_retweets_view(self, request):
        """Scan a Twitter/X account's timeline for retweets and archive any
        not already in the DB, as background job (see
        _run_account_retweets_job). Requires TWITTER_AUTH_TOKEN/TWITTER_CT0.
        """
        if not HAVE_TWITTER_GQL:
            return Response({'detail': 'twitter_gql_fetch module not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not _have_twitter_creds():
            return Response({'detail': 'Twitter credentials not configured on server'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        data = request.data if isinstance(request.data, dict) else {}
        screen_name = (data.get('screen_name') or '').strip().lstrip('@')
        if not screen_name:
            return Response({'detail': 'screen_name is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            max_items = int(data.get('max_items') or 30)
        except (TypeError, ValueError):
            max_items = 30

        try:
            threading.Thread(
                target=_run_account_retweets_job,
                args=(screen_name, max_items),
                daemon=True,
            ).start()
        except Exception:
            logging.exception('Failed to start background retweets fetch job for %s', screen_name)
            return Response({'detail': 'Failed to start background job'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'status': 'processing', 'screen_name': screen_name, 'max_items': max_items}, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['post'], url_path='scan_account_retweets')
    def scan_account_retweets_view(self, request):
        """Scans the account's timeline for retweets and creates a bare Item
        (no preview yet) for each one not already archived — image selection
        is deliberately NOT done here. The caller (RetweetFetchManager) is
        expected to run each returned item through the exact same
        fetch-then-review flow as any other link (fetchPreviewCandidates +
        the fetch queue — see FetchQueueManager.runBulkFetch), so RT-derived
        items go through identical image selection to everything else in
        the app rather than a bespoke path of their own.

        Runs synchronously (unlike fetch_account_retweets_view's background
        job) since it's just a handful of GraphQL page requests, no image
        downloads — should return in a few seconds even for max_items=40.
        """
        if not HAVE_TWITTER_GQL:
            return Response({'detail': 'twitter_gql_fetch module not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not _have_twitter_creds():
            return Response({'detail': 'Twitter credentials not configured on server'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        data = request.data if isinstance(request.data, dict) else {}
        screen_name = (data.get('screen_name') or '').strip().lstrip('@')
        if not screen_name:
            return Response({'detail': 'screen_name is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            max_items = int(data.get('max_items') or 30)
        except (TypeError, ValueError):
            max_items = 30

        try:
            result = fetch_account_retweets(screen_name, max_items=max_items)
        except TwitterAuthError as e:
            return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            logging.exception('Account retweets scan failed for %s', screen_name)
            return Response({'detail': f'Failed to fetch: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

        already_archived = 0
        created_items = []
        for rt in result.get('retweets', []):
            author = rt.get('screen_name') or ''
            tweet_id = rt.get('tweet_id')
            url = f'https://x.com/{author}/status/{tweet_id}' if author else f'https://x.com/i/status/{tweet_id}'
            if _find_item_by_url(url):
                already_archived += 1
                continue
            try:
                item = Item.objects.create(
                    external_id=int(tweet_id),
                    source='twitter_rt',
                    situation='',
                    titles=[],
                    characters=[],
                    artist=author,
                    link=url,
                    tags=None,
                    description=rt.get('description') or '',
                )
            except Exception:
                logging.exception('Failed to create Item for retweeted tweet %s', tweet_id)
                continue
            created_items.append({'id': item.id, 'link': item.link})

        return Response({
            'screen_name': result.get('screen_name'),
            'items': created_items,
            'already_archived': already_archived,
            'pages_fetched': result.get('pages_fetched', 0),
        })

    @action(detail=False, methods=['post'], url_path='fetch_account_bookmarks')
    def fetch_account_bookmarks_view(self, request):
        """Auto-mode background bookmark catch-up (see
        _run_account_bookmarks_job) — mirrors fetch_account_retweets_view,
        just for the logged-in account's own bookmarks (session-based; no
        screen_name needed). Requires TWITTER_AUTH_TOKEN/TWITTER_CT0. Most
        useful as a manual "catch up now" alongside poll_twitter_updates.py's
        own automatic per-tick discovery, e.g. right after a period where
        the poller itself couldn't authenticate and a backlog piled up.
        """
        if not HAVE_TWITTER_GQL:
            return Response({'detail': 'twitter_gql_fetch module not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not _have_twitter_creds():
            return Response({'detail': 'Twitter credentials not configured on server'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        data = request.data if isinstance(request.data, dict) else {}
        try:
            max_pages = int(data.get('max_pages') or 5)
        except (TypeError, ValueError):
            max_pages = 5
        max_pages = max(1, min(max_pages, 20))

        try:
            threading.Thread(
                target=_run_account_bookmarks_job,
                args=(max_pages,),
                daemon=True,
            ).start()
        except Exception:
            logging.exception('Failed to start background bookmarks fetch job')
            return Response({'detail': 'Failed to start background job'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'status': 'processing', 'max_pages': max_pages}, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['post'], url_path='scan_account_bookmarks')
    def scan_account_bookmarks_view(self, request):
        """Queue-mode bookmark scan — mirrors scan_account_retweets_view
        exactly (see its own docstring for why: bare Items only, no preview
        fetch here, so the caller runs each one through the normal
        fetch-then-review flow), just for the logged-in account's own
        bookmarks instead of a scanned account's timeline. Runs
        synchronously — a handful of GraphQL page requests, no image
        downloads.
        """
        if not HAVE_TWITTER_GQL:
            return Response({'detail': 'twitter_gql_fetch module not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not _have_twitter_creds():
            return Response({'detail': 'Twitter credentials not configured on server'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        data = request.data if isinstance(request.data, dict) else {}
        try:
            max_pages = int(data.get('max_pages') or 5)
        except (TypeError, ValueError):
            max_pages = 5
        max_pages = max(1, min(max_pages, 20))

        try:
            # Shares the poller's own pagination frontier — see
            # _get_bookmarks_resume_cursor's own docstring: this picks up
            # wherever poll_twitter_updates.py's incremental catch-up
            # currently stands instead of re-scanning ground it already
            # covered, and pushes that frontier further back in one go.
            candidates, resume_cursor = fetch_account_bookmarks(
                _known_twitter_ids(), max_pages=max_pages, start_cursor=_get_bookmarks_resume_cursor(),
            )
        except TwitterAuthError as e:
            return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            logging.exception('Account bookmarks scan failed')
            return Response({'detail': f'Failed to fetch: {e}'}, status=status.HTTP_502_BAD_GATEWAY)
        _save_bookmarks_resume_cursor(resume_cursor)

        already_archived = 0
        created_items = []
        for cand in candidates:
            author = cand.get('screen_name') or ''
            tweet_id = cand.get('tweet_id')
            url = f'https://x.com/{author}/status/{tweet_id}' if author else f'https://x.com/i/status/{tweet_id}'
            if _find_item_by_url(url):
                already_archived += 1
                continue
            try:
                item = Item.objects.create(
                    external_id=int(tweet_id),
                    source='twitter_bookmark',
                    situation='',
                    titles=[],
                    characters=[],
                    artist=author,
                    link=url,
                    tags=None,
                    description=cand.get('description') or '',
                )
            except Exception:
                logging.exception('Failed to create Item for bookmarked tweet %s', tweet_id)
                continue
            created_items.append({'id': item.id, 'link': item.link})

        return Response({
            'items': created_items,
            'already_archived': already_archived,
            'max_pages': max_pages,
        })

    @action(detail=True, methods=['post'], url_path='save_previews')
    def save_previews(self, request, pk=None):
        """Accepts client-provided images (data_uri) and persists them as PreviewImage.

        Supports chunked uploads (the frontend splits large/many images across
        several requests to stay under Cloudflare's ~100MB body limit):
        - `clear_existing` (default True): delete this item's existing previews
          before saving. Pass False for every chunk after the first so later
          chunks don't wipe out earlier ones.
        - `start_index`: offset added to each image's position to keep `order`
          correct across chunks (chunk 2 continues where chunk 1 left off).
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        images = data.get('images') or []
        if not isinstance(images, list) or not images:
            return Response({'detail': 'No images provided'}, status=status.HTTP_400_BAD_REQUEST)
        clear_existing = data.get('clear_existing', True)
        try:
            start_index = int(data.get('start_index') or 0)
        except (TypeError, ValueError):
            start_index = 0
        if clear_existing:
            PreviewImage.objects.filter(item=item).delete()
        saved = []
        for idx, img in enumerate(images):
            data_uri = img.get('data_uri') if isinstance(img, dict) else None
            url = img.get('url') if isinstance(img, dict) else None
            if data_uri:
                try:
                    import base64
                    header, b64 = data_uri.split(',', 1)
                    body = base64.b64decode(b64)
                    m = re.match(r'data:([^;]+);base64', header)
                    ctype = m.group(1) if m else 'application/octet-stream'
                    order = start_index + idx
                    PreviewImage.objects.create(item=item, order=order, data=body, content_type=ctype)
                    saved.append({'index': order, 'url': url, 'size': len(body), 'content_type': ctype})
                except Exception:
                    continue
        if not saved:
            return Response({'detail': 'No images saved'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response({'status': 'saved', 'count': len(saved), 'saved': saved})

    @action(detail=True, methods=['get', 'delete'], url_path='previews')
    def previews(self, request, pk=None):
        item = self.get_object()
        # DELETE on this collection endpoint removes all preview images for the item
        if request.method == 'DELETE':
            try:
                PreviewImage.objects.filter(item=item).delete()
                # clear any inline preview_data stored on the Item
                try:
                    item.preview_data = None
                    item.preview_content_type = None
                    item.save(update_fields=['preview_data', 'preview_content_type'])
                except Exception:
                    logging.exception('Failed to clear item.preview_data')
                return Response({'status': 'deleted', 'count': 0})
            except Exception as e:
                logging.exception('Failed to delete all previews')
                return Response({'detail': 'Failed to delete previews', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        imgs = item.preview_images.order_by('order')
        data = []
        for idx, img in enumerate(imgs):
            data.append({
                'id': img.id,
                'index': idx,
                'url': f"/api/items/{item.id}/previews/{idx}/",
                'content_type': img.content_type,
                'filename': getattr(img, 'filename', '') or '',
            })
        return Response(data)

    @action(detail=True, methods=['delete'], url_path='previews/id/(?P<pid>[^/]+)')
    def preview_delete_by_id(self, request, pk=None, pid=None):
        """DELETE a preview image by its database id for robustness against index drift."""
        item = self.get_object()
        try:
            pi = PreviewImage.objects.get(pk=int(pid), item=item)
        except Exception:
            return Response({'detail': 'preview not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            _remove_preview_images(item, [pi.pk])
            return Response({'status': 'deleted', 'id': pid})
        except Exception as e:
            logging.exception('Failed to delete preview image by id')
            return Response({'detail': 'Failed to delete preview', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get', 'delete'], url_path='previews/(?P<idx>[^/]+)')
    def preview_index(self, request, pk=None, idx=None):
        item = self.get_object()
        try:
            idxi = int(idx)
        except Exception:
            return Response({'detail': 'invalid index'}, status=status.HTTP_400_BAD_REQUEST)
        imgs = list(item.preview_images.order_by('order'))
        if idxi < 0 or idxi >= len(imgs):
            return Response({'detail': 'index out of range'}, status=status.HTTP_404_NOT_FOUND)
        # DELETE: remove a single preview image at the given index
        if request.method == 'DELETE':
            try:
                _remove_preview_images(item, [imgs[idxi].pk])
                return Response({'status': 'deleted', 'index': idxi})
            except Exception as e:
                logging.exception('Failed to delete preview image')
                return Response({'detail': 'Failed to delete preview', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # GET: return the image bytes for the requested index
        img = imgs[idxi]
        return HttpResponse(img.data, content_type=img.content_type or 'application/octet-stream')

    @action(detail=True, methods=['post'], url_path='delete_previews')
    def delete_previews(self, request, pk=None):
        """Delete several of this item's images in one call.

        Body: exactly one of
          {"delete_ids": [PreviewImage id, ...]}  -- remove these;
          {"keep_ids":   [PreviewImage id, ...]}  -- keep ONLY these, remove
                                                      every other image.
        "Keep" exists because the common case is "this post has 4 images and
        I want just the one" -- naming the survivor is one click where
        ticking the other three is three. keep_ids must be non-empty (an
        empty keep would delete everything; the explicit clear-all endpoint
        already exists for that) and every id must belong to this item.
        Returns the _remove_preview_images summary, including how many
        region labels were dropped/renumbered along with the images.
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        has_delete, has_keep = 'delete_ids' in data, 'keep_ids' in data
        if has_delete == has_keep:
            return Response({'detail': 'give exactly one of delete_ids / keep_ids'}, status=status.HTTP_400_BAD_REQUEST)
        key = 'delete_ids' if has_delete else 'keep_ids'
        raw = data.get(key)
        if not isinstance(raw, list):
            return Response({'detail': f'{key} must be a list'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            wanted = {int(v) for v in raw}
        except (TypeError, ValueError):
            return Response({'detail': f'{key} must be a list of ids'}, status=status.HTTP_400_BAD_REQUEST)

        own = set(item.preview_images.values_list('id', flat=True))
        if not wanted <= own:
            return Response({'detail': 'some ids do not belong to this item'}, status=status.HTTP_400_BAD_REQUEST)
        if has_keep:
            if not wanted:
                return Response({'detail': 'keep_ids must not be empty (use the clear-all endpoint to delete everything)'},
                                status=status.HTTP_400_BAD_REQUEST)
            doomed = own - wanted
        else:
            doomed = wanted
        try:
            result = _remove_preview_images(item, doomed)
        except Exception as e:
            logging.exception('Failed to delete previews in bulk for item %s', item.pk)
            return Response({'detail': 'Failed to delete previews', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'status': 'deleted', **result})

    @action(detail=True, methods=['post'], url_path='reorder_previews')
    def reorder_previews(self, request, pk=None):
        """Reorder this item's preview images.

        Body accepts:
          {"order_ids": [id1, id2, ...]} -- explicit new order by PreviewImage id
          OR
          {"action": "sort_by_filename", "reverse": false} -- sort by PreviewImage.filename naturally
          OR
          {"action": "reverse"} -- reverse current order
          OR
          {"action": "move", "preview_id": id, "direction": -1 | 1} -- move image backward/forward
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        action_name = data.get('action')

        rows = list(item.preview_images.order_by('order'))
        if not rows:
            return Response({'status': 'ok', 'previews': []})

        if action_name == 'sort_by_filename':
            reverse = bool(data.get('reverse', False))
            sorted_rows = sorted(rows, key=lambda img: _natural_sort_key(img.filename or f"img_{img.order:05d}"), reverse=reverse)
            new_ordered_ids = [r.id for r in sorted_rows]
        elif action_name == 'reverse':
            new_ordered_ids = [r.id for r in reversed(rows)]
        elif action_name == 'move':
            pid = data.get('preview_id')
            direction = int(data.get('direction', 0))
            current_ids = [r.id for r in rows]
            try:
                curr_idx = current_ids.index(int(pid))
                new_idx = curr_idx + direction
                if 0 <= new_idx < len(current_ids):
                    current_ids[curr_idx], current_ids[new_idx] = current_ids[new_idx], current_ids[curr_idx]
                new_ordered_ids = current_ids
            except (ValueError, TypeError):
                return Response({'detail': 'invalid preview_id'}, status=status.HTTP_400_BAD_REQUEST)
        elif 'order_ids' in data:
            order_ids = data.get('order_ids')
            if not isinstance(order_ids, list):
                return Response({'detail': 'order_ids must be a list of ids'}, status=status.HTTP_400_BAD_REQUEST)
            new_ordered_ids = [int(i) for i in order_ids]
        else:
            return Response({'detail': 'must provide action or order_ids'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = _reorder_preview_images(item, new_ordered_ids)
        except Exception as e:
            logging.exception('Failed to reorder previews for item %s', item.pk)
            return Response({'detail': 'Failed to reorder previews', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        updated_imgs = item.preview_images.order_by('order')
        preview_list = [
            {'id': img.id, 'index': idx, 'url': f"/api/items/{item.id}/previews/{idx}/", 'content_type': img.content_type, 'filename': getattr(img, 'filename', '') or ''}
            for idx, img in enumerate(updated_imgs)
        ]
        return Response({'status': 'reordered', **result, 'previews': preview_list})

    @action(detail=True, methods=['delete'], url_path='delete_item')
    def delete_item(self, request, pk=None):
        """Delete the Item and all associated preview images from the database."""
        item = self.get_object()
        try:
            item_id = item.id
            item.delete()
            return Response({'status': 'deleted', 'id': item_id})
        except Exception as e:
            logging.exception('Failed to delete Item %s', pk)
            return Response({'detail': 'Failed to delete item', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=False, methods=['get'], url_path='tagger_capabilities')
    def tagger_capabilities(self, request):
        """Whether the optional 'timm' tagger backend is installed on this
        server (see tagger.py's HAVE_TIMM) — lets the edit form/edit queue
        only offer that model choice where it'll actually work, instead of
        every deployment showing an option that 422s on Pi-class installs
        that never opted into the heavier dependency (see requirements-timm.txt).
        """
        return Response({'have_timm': bool(HAVE_TAGGER and getattr(tagger, 'HAVE_TIMM', False))})

    @action(detail=False, methods=['get'], url_path='all_titles')
    def all_titles(self, request):
        """Return all unique titles used across all items, sorted alphabetically."""
        all_titles = set()
        for item in Item.objects.only('titles'):
            if isinstance(item.titles, list):
                for t in item.titles:
                    if t and isinstance(t, str):
                        all_titles.add(t.strip())
        return Response(sorted(all_titles))

    @action(detail=False, methods=['get'], url_path='all_characters')
    def all_characters(self, request):
        """Return all unique characters used across all items, sorted alphabetically."""
        all_chars = set()
        for item in Item.objects.only('characters'):
            if isinstance(item.characters, list):
                for c in item.characters:
                    if c and isinstance(c, str):
                        all_chars.add(c.strip())
        return Response(sorted(all_chars))

    @action(detail=False, methods=['get'], url_path='incomplete')
    def incomplete(self, request):
        """Items missing one or more metadata fields — feeds the bulk edit
        queue (mailbox-style review UI), as an alternative to opening
        EditFields one item at a time.

        Query param `missing`: comma-separated subset of
        titles,characters,tags,situation,artist. Defaults to all five.

        Query param `before_id`: only return items with id < before_id.
        Deliberately NOT using DRF's page-number pagination here: as the
        queue is worked through, items get edited and stop matching this
        filter, which shrinks the underlying queryset out from under an
        offset/page-number cursor — the classic symptom being an entire
        batch silently skipped the moment you ask for "the next page" after
        finishing the current one. A same-direction id cutoff isn't
        affected by rows disappearing above it, so nothing gets skipped
        (or repeated) as the queue is worked through.
        """
        valid_fields = ('titles', 'characters', 'tags', 'situation', 'artist')
        requested = (request.GET.get('missing') or ','.join(valid_fields)).split(',')
        fields = [f.strip() for f in requested if f.strip() in valid_fields] or list(valid_fields)

        q = Q()
        for f in fields:
            if f in ('situation', 'artist'):
                q |= Q(**{f: ''}) | Q(**{f'{f}__isnull': True})
            else:
                # JSONField list (titles/characters/tags): empty means [] or null
                q |= Q(**{f: []}) | Q(**{f'{f}__isnull': True})

        queryset = Item.objects.filter(q).order_by('-id')
        total_count = queryset.count()

        before_id = request.GET.get('before_id')
        if before_id:
            try:
                queryset = queryset.filter(id__lt=int(before_id))
            except (TypeError, ValueError):
                pass

        page_size = 50
        batch = list(queryset[:page_size + 1])
        has_more = len(batch) > page_size
        batch = batch[:page_size]

        serializer = self.get_serializer(batch, many=True)
        return Response({
            'results': serializer.data,
            'count': total_count,
            'has_more': has_more,
            'next_before_id': batch[-1].id if (has_more and batch) else None,
        })

    @action(detail=False, methods=['get'], url_path='region_label_queue')
    def region_label_queue(self, request):
        """Items eligible for manual multi-character region labeling that
        haven't been touched AT ALL yet (`character_regions == []`) — feeds
        RegionLabelQueueManager.jsx's "未ラベル" mode, a bulk review UI in
        the same spirit as `incomplete` above (see its own docstring for
        why `before_id` cursoring is used instead of page-number
        pagination — the same "items stop matching as they're worked
        through" problem applies here identically: labeling an item empties
        it out of this queryset).

        Deliberately narrow — "touched at all" (even a single box saved) is
        enough to graduate an item OUT of this queue for good, whether or
        not every confirmed character ended up with a box. It used to also
        include partially-labeled items still missing a box for someone,
        which sounds right in isolation, but in practice meant an item you
        already made a considered decision about in region_mismatch_queue
        ("this person just isn't boxed and that's fine") would silently
        reappear back HERE the next time you opened this tab — reported as
        "already-resolved items keep coming back, very stressful". Once an
        item has character_regions at all, region_mismatch_queue is the
        ONE place any remaining gap gets tracked and resolved from now on
        (see its own docstring) — this queue never looks at it again.

        Excludes situation SOLO (only one person — nothing to disambiguate),
        and anything with no image to annotate at all.
        """
        queryset = (
            Item.objects.exclude(situation='SOLO')
            .filter(character_regions=[])
            .exclude(Q(preview_images__isnull=True) & Q(preview_data__isnull=True))
            .order_by('-id')
            .distinct()
        )
        total_count = queryset.count()

        before_id = request.GET.get('before_id')
        if before_id:
            try:
                queryset = queryset.filter(id__lt=int(before_id))
            except (TypeError, ValueError):
                pass

        page_size = 50
        batch = list(queryset[:page_size + 1])
        has_more = len(batch) > page_size
        batch = batch[:page_size]

        serializer = self.get_serializer(batch, many=True)
        return Response({
            'results': serializer.data,
            'count': total_count,
            'has_more': has_more,
            'next_before_id': batch[-1].id if (has_more and batch) else None,
        })

    @action(detail=False, methods=['get'], url_path='needs_attention_queue')
    def needs_attention_queue(self, request):
        """Items that need SOME kind of human attention — either missing
        metadata (see `incomplete`'s own docstring) OR eligible for
        multi-character region labeling but never touched yet (see
        `region_label_queue`'s own docstring) — feeds ItemQueueManager.jsx's
        unified '編集キュー' (the two used to be worked as separate queues,
        forcing the same image open twice; see that component's own
        top-level comment). Server-cursor mode only (see `incomplete` for
        why `before_id`, not page-number pagination); the ordinary
        allItems-scoped mode filters client-side instead, mirroring this
        exact OR of the two underlying predicates.

        Deliberately does NOT fold in region_mismatch_queue's condition —
        that stays its own separate tab/queue (see ItemQueueManager.jsx's
        'mismatch' mode and this class's region_mismatch_queue action),
        since it's a fundamentally different follow-up task (reconciling
        two already-recorded answers that disagree) rather than "nothing
        has been recorded yet".

        Query param `missing`: same comma-separated subset of
        titles,characters,tags,situation,artist as `incomplete` — also
        controls which fields are reported per result in `missing_fields`.
        """
        valid_fields = ('titles', 'characters', 'tags', 'situation', 'artist')
        requested = (request.GET.get('missing') or ','.join(valid_fields)).split(',')
        fields = [f.strip() for f in requested if f.strip() in valid_fields] or list(valid_fields)

        missing_q = Q()
        for f in fields:
            if f in ('situation', 'artist'):
                missing_q |= Q(**{f: ''}) | Q(**{f'{f}__isnull': True})
            else:
                missing_q |= Q(**{f: []}) | Q(**{f'{f}__isnull': True})

        region_q = (
            Q(character_regions=[])
            & ~Q(situation='SOLO')
            & ~(Q(preview_images__isnull=True) & Q(preview_data__isnull=True))
        )

        queryset = Item.objects.filter(missing_q | region_q).order_by('-id').distinct()
        total_count = queryset.count()

        before_id = request.GET.get('before_id')
        if before_id:
            try:
                queryset = queryset.filter(id__lt=int(before_id))
            except (TypeError, ValueError):
                pass

        page_size = 50
        batch = list(queryset[:page_size + 1])
        has_more = len(batch) > page_size
        batch = batch[:page_size]

        serializer = self.get_serializer(batch, many=True)
        results = serializer.data
        for entry, item in zip(results, batch):
            entry['missing_fields'] = [f for f in fields if _is_field_missing(item, f)]
            # Re-derive from the already-serialized `has_preview` rather than
            # re-querying preview existence — keeps this in exact lockstep
            # with region_q's own preview-existence condition above without
            # a second DB hit per item.
            entry['needs_region'] = (
                item.situation != 'SOLO'
                and not item.character_regions
                and bool(entry.get('has_preview'))
            )

        return Response({
            'results': results,
            'count': total_count,
            'has_more': has_more,
            'next_before_id': batch[-1].id if (has_more and batch) else None,
        })

    @action(detail=False, methods=['get'], url_path='region_mismatch_queue')
    def region_mismatch_queue(self, request):
        """Items that HAVE been touched by region labeling
        (`character_regions` non-empty) but where the character set it
        implies still doesn't exactly match Item.characters, in EITHER
        direction — feeds RegionLabelQueueManager.jsx's "不整合あり" mode.
        This is the ONE place any gap on a touched item gets tracked and
        resolved from here on — region_label_queue's "未ラベル" mode only
        ever looks at completely untouched items (see its own docstring for
        why splitting "untouched" from "touched but incomplete" this way
        was itself the fix for a real complaint: a considered decision made
        here kept getting silently undone by re-appearing back in 未ラベル).

        A resolution here can go three ways, and NONE of them send the item
        back to 未ラベル (it already has character_regions, so it can
        never match that queue's filter again):
          - sync_characters_to_regions: adopt the region's list wholesale
            (item.characters := region_chars exactly) — trusts the boxes
            over whatever's in the edit queue's list, full stop.
          - acknowledge_character_mismatch: keep item.characters exactly as
            it is and just record that a human looked at this specific
            gap and accepted it (e.g. "this confirmed character genuinely
            isn't boxed in this picture, and that's fine") — see
            character_regions_ack_signature's own docstring for how this
            avoids either a full data change or the item nagging again
            after a real, later change.
          - Keep annotating right here (RegionLabelQueueManager.jsx embeds
            RegionAnnotator in this mode too) or free-hand edit
            item.characters (update_fields) — either way, once the two
            sides actually agree, this item just stops matching below on
            its own, no bookkeeping needed.

        Can't express "two JSON-derived sets are unequal" as a single
        portable DB query, so this filters in Python — fine at this app's
        scale, since the candidate set (items with any character_regions at
        all) is already a small subset of all items. Same before_id
        cursoring as the other queue actions, applied to the
        already-computed mismatch list.
        """
        candidates = Item.objects.exclude(character_regions=[]).order_by('-id')
        mismatched = []
        for item in candidates.iterator():
            region_chars = {c for r in (item.character_regions or []) for c in (r.get('characters') or [])}
            item_chars = set(item.characters or [])
            if region_chars == item_chars:
                continue
            if _char_diff_signature(region_chars, item_chars) == item.character_regions_ack_signature:
                continue
            mismatched.append(item)

        total_count = len(mismatched)

        before_id = request.GET.get('before_id')
        if before_id:
            try:
                bid = int(before_id)
                mismatched = [it for it in mismatched if it.id < bid]
            except (TypeError, ValueError):
                pass

        page_size = 50
        batch = mismatched[:page_size + 1]
        has_more = len(batch) > page_size
        batch = batch[:page_size]

        serialized = self.get_serializer(batch, many=True).data
        for entry, item in zip(serialized, batch):
            region_chars = {c for r in (item.character_regions or []) for c in (r.get('characters') or [])}
            item_chars = set(item.characters or [])
            entry['region_only_characters'] = sorted(region_chars - item_chars)
            entry['item_only_characters'] = sorted(item_chars - region_chars)

        return Response({
            'results': serialized,
            'count': total_count,
            'has_more': has_more,
            'next_before_id': batch[-1].id if (has_more and batch) else None,
        })

    @action(detail=True, methods=['post'], url_path='assign_character_groups')
    def assign_character_groups(self, request, pk=None):
        """File characters into the groups a person chose -- the answer to the
        `character_group_choice` an earlier save returned when this item's
        titles led to more than one possible group.

        Body: {"assignments": [{"character": str, "group_id": int} |
                                {"character": str, "create_for_title": str}, ...]}.
        `create_for_title` makes (or reuses) a group named after one of THIS
        item's titles, for a title that had no group. Characters that are not
        on the item, or are already in some group, are skipped (a person's
        earlier placement is never moved); the response says which.
        """
        from .character_groups import file_characters

        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        assignments = data.get('assignments')
        if not isinstance(assignments, list):
            return Response({'detail': 'assignments must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        item_chars = set(item.characters or [])
        item_titles = {t for t in (item.titles or []) if t}
        grouped = {c for g in CharacterGroup.objects.all() for c in (g.characters or [])}
        plan = defaultdict(list)  # ('group', id) | ('title', name) -> [characters]
        skipped = []
        for a in assignments:
            if not isinstance(a, dict) or not isinstance(a.get('character'), str):
                return Response({'detail': 'each assignment needs a character'}, status=status.HTTP_400_BAD_REQUEST)
            char = a['character']
            if char not in item_chars or char in grouped:
                skipped.append(char)
                continue
            if a.get('create_for_title') is not None:
                title = a['create_for_title']
                if title not in item_titles:
                    return Response({'detail': 'create_for_title must be one of this item\'s titles'}, status=status.HTTP_400_BAD_REQUEST)
                plan[('title', title)].append(char)
            elif a.get('group_id') is not None:
                plan[('group', a['group_id'])].append(char)
            else:
                return Response({'detail': 'each assignment needs group_id or create_for_title'}, status=status.HTTP_400_BAD_REQUEST)

        # validate every target before writing any of them
        targets = {}
        for kind, ref in plan:
            if kind == 'group':
                try:
                    targets[(kind, ref)] = CharacterGroup.objects.get(pk=ref)
                except (CharacterGroup.DoesNotExist, ValueError, TypeError):
                    return Response({'detail': f'group {ref} not found'}, status=status.HTTP_404_NOT_FOUND)

        filed, created = [], []
        for (kind, ref), chars in plan.items():
            if kind == 'group':
                group, was_created = file_characters(targets[(kind, ref)], chars)
            else:
                group, was_created = file_characters(None, chars, create_title=ref)
            filed.append({'group_id': group.id, 'group_name': group.name, 'characters': chars})
            if was_created:
                created.append({'id': group.id, 'name': group.name})
        return Response({'status': 'ok', 'filed': filed, 'created_groups': created, 'skipped': skipped})

    @action(detail=True, methods=['post'], url_path='sync_characters_to_regions')
    def sync_characters_to_regions(self, request, pk=None):
        """Set Item.characters to EXACTLY the character set implied by
        Item.character_regions — the "領域指定キューを採用" resolution
        offered by region_mismatch_queue, for when a human has compared
        both full lists and decided the region boxes are the ones to
        trust: any item-only name (confirmed but never boxed) is dropped,
        any region-only name (boxed but not in item.characters) is added.
        A full, deliberate overwrite — not the ADD-only merge
        character_regions_view's own save uses — because this is only ever
        reached after a human has actually looked at both lists side by
        side in the mismatch review UI, not as an automatic side effect.
        """
        item = self.get_object()
        region_chars = sorted({c for r in (item.character_regions or []) for c in (r.get('characters') or [])})
        introduced_chars = [c for c in region_chars if c not in (item.characters or [])]
        item.characters = region_chars
        item.character_regions_ack_signature = ''  # now genuinely equal — nothing left to remember
        item.save(update_fields=['characters', 'character_regions_ack_signature'])
        grouping = _group_response_fields(ensure_characters_grouped(item, only=introduced_chars))
        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'saved', 'item': serializer.data, **grouping})

    @action(detail=True, methods=['post'], url_path='acknowledge_character_mismatch')
    def acknowledge_character_mismatch(self, request, pk=None):
        """Record that a human looked at THIS item's current region vs.
        item.characters gap and decided item.characters is fine as-is —
        the "編集キューを採用" resolution offered by region_mismatch_queue,
        for when the gap is something like "this confirmed character just
        isn't boxed in this particular picture" rather than an actual
        error. Changes no data at all (see sync_characters_to_regions for
        the resolution that does); just stores a content fingerprint of the
        current (region_chars, item.characters) pair so region_mismatch_
        queue stops re-flagging THIS SPECIFIC gap — see character_regions_
        ack_signature's own docstring for why a later, genuinely new change
        on either side automatically starts flagging it again with no
        extra bookkeeping.
        """
        item = self.get_object()
        region_chars = {c for r in (item.character_regions or []) for c in (r.get('characters') or [])}
        item_chars = set(item.characters or [])
        item.character_regions_ack_signature = _char_diff_signature(region_chars, item_chars)
        item.save(update_fields=['character_regions_ack_signature'])
        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'saved', 'item': serializer.data})

    @action(detail=True, methods=['post'], url_path='suggest_tags')
    def suggest_tags_view(self, request, pk=None):
        """Suggest titles/characters/tags/situation for this item — a thin
        HTTP wrapper around _suggest_for_item_ensemble (the production
        default — every source's weighted confidence is combined) or
        _suggest_for_item (opt-out via `use_ensemble: false` — "first
        source to resolve a field wins"). Both are module-level functions
        below and are also called directly by item.management.commands.
        evaluate_full_pipeline/evaluate_ensemble for offline accuracy
        evaluation against an in-memory (never-saved) item, without going
        through a live request.

        use_ensemble defaults to True as of 2026-09 — a real 10-seed
        evaluation showed the ensemble winning on character accuracy in
        every sampled seed (see _suggest_for_item_ensemble's docstring),
        and it has since become the default everywhere this endpoint is
        called from (both EditFields.jsx and EditQueueManager.jsx default
        their own checkboxes to checked too). The cascade remains
        available as an explicit opt-out, not removed.
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        external = bool(data.get('external'))
        use_ensemble = bool(data.get('use_ensemble', True))
        tagger_backend = 'timm' if data.get('model') == 'timm' else 'onnx'
        if tagger_backend == 'timm' and not getattr(tagger, 'HAVE_TIMM', False):
            return Response(
                {'detail': 'この最新モデル(timm)はサーバーにインストールされていません。管理者に環境構築を依頼してください。'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        try:
            general_threshold = float(data.get('general_threshold', 0.35))
            character_threshold = float(data.get('character_threshold', 0.85))
        except (TypeError, ValueError):
            general_threshold, character_threshold = 0.35, 0.85

        # 0-based position within item.preview_images (same indexing as
        # the `preview`/`previews` actions' own ?index=N / previews/{idx}/)
        # — lets the client run inference against a specific image instead
        # of always the single largest one. Omitted/invalid falls back to
        # that same default (see _select_image_bytes).
        image_index = data.get('image_index')
        if image_index is not None:
            try:
                image_index = int(image_index)
            except (TypeError, ValueError):
                image_index = None

        # How many of the post's images to analyse when none was picked (the
        # ensemble path only; the cascade stays single-image). Clamped: each
        # image is a full, slow tagger pass.
        try:
            max_images = int(data.get('max_images') or DEFAULT_MAX_IMAGES)
        except (TypeError, ValueError):
            max_images = DEFAULT_MAX_IMAGES
        max_images = max(1, min(max_images, MAX_IMAGES_LIMIT))

        if use_ensemble:
            result = _suggest_for_item_ensemble(
                item, external=external, tagger_backend=tagger_backend,
                general_threshold=general_threshold, character_threshold=character_threshold,
                image_index=image_index, max_images=max_images,
            )
        else:
            result = _suggest_for_item(
                item, external=external, tagger_backend=tagger_backend,
                general_threshold=general_threshold, character_threshold=character_threshold,
                image_index=image_index,
            )
        return Response(result)

    @action(detail=True, methods=['post'], url_path='update_fields')
    def update_fields(self, request, pk=None):
        """Update editable JSON fields on an Item (characters, tags, titles).

        Expects JSON body with any of: `characters` (list), `tags` (list|null), `titles` (list), `situation` (string).
        Returns the updated serialized item on success.
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        updates = {}
        orig_titles = list(item.titles or [])
        orig_characters = list(item.characters or [])

        if 'characters' in data:
            chars = data.get('characters')
            if not isinstance(chars, list):
                return Response({'detail': 'characters must be a list'}, status=status.HTTP_400_BAD_REQUEST)
            item.characters = chars
            updates['characters'] = chars

        if 'tags' in data:
            tags = data.get('tags')
            if tags is not None and not isinstance(tags, list):
                return Response({'detail': 'tags must be a list or null'}, status=status.HTTP_400_BAD_REQUEST)
            item.tags = tags
            updates['tags'] = tags

        if 'titles' in data:
            titles = data.get('titles')
            if not isinstance(titles, list):
                return Response({'detail': 'titles must be a list'}, status=status.HTTP_400_BAD_REQUEST)
            item.titles = titles
            updates['titles'] = titles

        if 'situation' in data:
            situation = data.get('situation')
            if situation is None:
                situation = ''
            if not isinstance(situation, str):
                return Response({'detail': 'situation must be a string'}, status=status.HTTP_400_BAD_REQUEST)
            item.situation = situation.strip().upper()
            updates['situation'] = item.situation

        if 'artist' in data:
            artist = data.get('artist')
            if artist is None:
                artist = ''
            if not isinstance(artist, str):
                return Response({'detail': 'artist must be a string'}, status=status.HTTP_400_BAD_REQUEST)
            item.artist = artist.strip()
            updates['artist'] = item.artist

        if not updates:
            return Response({'detail': 'No updatable fields provided'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item.save()
        except Exception as e:
            logging.exception('Failed to save Item updates')
            return Response({'detail': 'Failed to save', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Every character this item carries ends up in its title's group --
        # including ones typed before the title was, which is why a title
        # change alone re-runs this (see item.character_groups).
        grouping = {}
        if 'characters' in updates or 'titles' in updates:
            # When the titles don't settle where a character goes, the person is
            # asked (see character_group_choice) -- about the characters this
            # save introduced, or about every ungrouped one if the titles
            # themselves just changed (that is what makes them askable now).
            introduced = [c for c in (item.characters or []) if c not in orig_characters]
            titles_changed = set(t for t in (item.titles or []) if t) != set(t for t in orig_titles if t)
            grouping = _group_response_fields(
                ensure_characters_grouped(item, only=None if titles_changed else introduced))

        serializer = ItemSerializer(item, context={'request': request})
        response = {'status': 'updated', 'updated': updates, 'item': serializer.data, **grouping}
        return Response(response)

    @action(detail=False, methods=['post'], url_path='create_manual')
    def create_manual(self, request):
        """Manually register a new Item from locally-held image file(s) —
        for when the original post's URL is dead (deleted/suspended/404)
        but the image was already saved somewhere before that happened.
        Bypasses the entire fetch pipeline: images come straight from this
        multipart upload, never from any URL, so an unreachable link is a
        non-issue. Reuses the plain viewer/edit-form flow for everything
        after creation — titles/characters/tags/situation are left empty
        here on purpose, exactly like any other freshly-fetched item
        waiting in the edit queue.

        Multipart form fields:
          images: one or more image files (required — at least one)
          link:   optional — the original (now-dead) post URL, kept only
                  as a reference/citation, never fetched from
          artist: optional
          source: optional, defaults to 'manual'
        """
        files = request.FILES.getlist('images')
        if not files:
            return Response({'detail': '画像ファイルを1枚以上指定してください'}, status=status.HTTP_400_BAD_REQUEST)

        for f in files:
            if not (f.content_type or '').startswith('image/'):
                return Response({'detail': f'{f.name} は画像ファイルではありません'}, status=status.HTTP_400_BAD_REQUEST)
            if f.size > MAX_MANUAL_UPLOAD_BYTES:
                return Response(
                    {'detail': f'{f.name} が大きすぎます(1ファイルの上限{MAX_MANUAL_UPLOAD_BYTES // (1024 * 1024)}MB)'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        data = request.data
        link = (data.get('link') or '').strip()
        artist = (data.get('artist') or '').strip()
        source = (data.get('source') or 'manual').strip() or 'manual'

        item = Item.objects.create(
            # No real external id exists for a manually-registered item — a
            # millisecond timestamp is unique enough in practice. Harmless
            # even if it happened to coincide with some other source's id,
            # since every lookup that matches by external_id in this app
            # (_find_item_by_url and friends) always filters by `source`
            # too, and nothing ever looks up a 'manual' item that way.
            external_id=int(timezone.now().timestamp() * 1000),
            source=source,
            situation='',
            titles=[],
            characters=[],
            artist=artist,
            link=link,
            tags=None,
        )

        for idx, f in enumerate(files):
            try:
                PreviewImage.objects.create(item=item, order=idx, data=f.read(), content_type=f.content_type or 'image/jpeg', filename=f.name or '')
            except Exception:
                logging.exception('Failed to save manually-uploaded image %s for item %s', f.name, item.pk)

        if not item.preview_images.exists():
            item.delete()
            return Response({'detail': '画像の保存に失敗しました'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'created', 'item': serializer.data}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='upload_preview')
    def upload_preview(self, request, pk=None):
        """Attach locally-held image file(s) to an EXISTING item — for
        when the item itself is already registered (e.g. via a Danbooru
        link, or a fetch that only got tags/metadata) but has no preview
        because its source link is dead/private, while the user happens
        to have the actual image saved elsewhere. Mirrors create_manual's
        file-upload/validation (same MAX_MANUAL_UPLOAD_BYTES cap, same
        image/* content-type check) but appends PreviewImage rows to an
        item that already exists instead of creating a new one.

        Multipart form fields:
          images: one or more image files (required — at least one)

        Appends after any existing preview images (order = current count
        + index) rather than replacing them — a caller that specifically
        wants a clean slate first should DELETE previews/ before calling
        this, exactly like clearing before a normal re-fetch.
        """
        item = self.get_object()

        files = request.FILES.getlist('images')
        if not files:
            return Response({'detail': '画像ファイルを1枚以上指定してください'}, status=status.HTTP_400_BAD_REQUEST)

        for f in files:
            if not (f.content_type or '').startswith('image/'):
                return Response({'detail': f'{f.name} は画像ファイルではありません'}, status=status.HTTP_400_BAD_REQUEST)
            if f.size > MAX_MANUAL_UPLOAD_BYTES:
                return Response(
                    {'detail': f'{f.name} が大きすぎます(1ファイルの上限{MAX_MANUAL_UPLOAD_BYTES // (1024 * 1024)}MB)'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        start_order = item.preview_images.count()
        created = 0
        for offset, f in enumerate(files):
            try:
                PreviewImage.objects.create(
                    item=item, order=start_order + offset,
                    data=f.read(), content_type=f.content_type or 'image/jpeg',
                    filename=f.name or '',
                )
                created += 1
            except Exception:
                logging.exception('Failed to save uploaded image %s for item %s', f.name, item.pk)

        if created == 0:
            return Response({'detail': '画像の保存に失敗しました'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'uploaded', 'added': created, 'item': serializer.data})

    @action(detail=True, methods=['post'], url_path='salvage_pixiv')
    def salvage_pixiv(self, request, pk=None):
        """Recover a DELETED Pixiv artwork's original image(s) straight
        from Pixiv's CDN (see item.pixiv_salvage's own docstring for how)
        -- for when the item's link is a pixiv.net artwork that's gone
        (404/removed) and the user never saved a copy locally either (if
        they had, upload_preview above is the right tool instead).

        Runs synchronously and can genuinely take a while (a brute-force
        second-by-second search over the gap between the nearest still-
        existing neighboring posts) -- item.pixiv_salvage.MAX_BRACKET_SECONDS
        refuses up front rather than grinding for a very long time, but
        even a search within that cap can take real minutes.

        Appends recovered images after any existing previews (same
        append-don't-replace convention as upload_preview) rather than
        assuming there are none -- a caller that specifically wants a
        clean slate first should DELETE previews/ before calling this.
        """
        item = self.get_object()

        match = re.search(r'/artworks/(\d+)', item.link or '')
        if not match:
            return Response(
                {'detail': 'このアイテムのリンクはPixivの作品URL(pixiv.net/artworks/12345)ではありません'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artwork_id = int(match.group(1))

        try:
            urls = pixiv_salvage.discover_salvage_urls(artwork_id)
        except pixiv_salvage.PixivSalvageError as e:
            return Response({'detail': str(e)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as e:
            logging.exception('Pixiv salvage failed for item %s', item.pk)
            return Response({'detail': 'サルベージに失敗しました', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        images = pixiv_salvage.fetch_salvaged_images(urls)
        if not images:
            return Response({'detail': '画像の発見には成功しましたが、ダウンロードに失敗しました'}, status=status.HTTP_502_BAD_GATEWAY)

        start_order = item.preview_images.count()
        created = 0
        for offset, (data, content_type) in enumerate(images):
            try:
                PreviewImage.objects.create(item=item, order=start_order + offset, data=data, content_type=content_type)
                created += 1
            except Exception:
                logging.exception('Failed to save salvaged image for item %s', item.pk)

        if created == 0:
            return Response({'detail': '画像の保存に失敗しました'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'salvaged', 'found': len(urls), 'added': created, 'item': serializer.data})

    @action(detail=True, methods=['post'], url_path='detect_regions')
    def detect_regions(self, request, pk=None):
        """Person-detection candidate boxes for one of this item's images —
        feeds RegionAnnotator.jsx's "自動検出" button. Body: `{image_index:
        int|null}`, same 0-based/order-sorted indexing as ItemViewSet.preview's
        own ?index=N (None = the largest image, matching _select_image_bytes'
        default elsewhere). Returns `{image_index, boxes}` where each box is
        `[x1, y1, x2, y2]` in that image's own absolute pixel coordinates —
        the frontend still has to fetch the image itself (via /preview/) to
        know its natural dimensions for overlay scaling.

        Detection failing isn't an error (tagger._detect_person_boxes never
        raises — see its own docstring) — an empty `boxes` list just means
        the user draws every box manually instead.
        """
        if not HAVE_TAGGER:
            return Response({'detail': 'Tagger module not available on this server'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        image_index = data.get('image_index')
        if image_index is not None:
            try:
                image_index = int(image_index)
            except (TypeError, ValueError):
                image_index = None

        image_bytes, resolved_index = _select_image_bytes(item, image_index)
        if image_bytes is None:
            return Response({'detail': 'No image available for this item'}, status=status.HTTP_404_NOT_FOUND)

        try:
            boxes = tagger._detect_person_boxes(image_bytes)
        except Exception:
            # Matches every other call site of this function (see
            # tagger.py's suggest_tags) — its docstring's "never raises"
            # contract is actually enforced by the caller, not the
            # function itself (e.g. imgutils not being installed raises
            # here), so this needs the same try/except those have.
            logging.exception('detect_regions: person detection failed')
            boxes = []

        proposed_regions = []
        if boxes and HAVE_TAGGER:
            try:
                tagger_backend = 'onnx'
                item_chars = list(item.characters or [])
                crops = []
                for box in boxes[:tagger._MAX_PERSON_CROPS]:
                    try:
                        crop_bytes = tagger._crop_with_padding(image_bytes, box)
                        crop_result = tagger._suggest_tags_single_pass(
                            crop_bytes, 0.35, 0.85, 15, backend=tagger_backend,
                        )
                        crops.append({
                            'box': list(box),
                            'characters': crop_result.get('characters', []),
                            'general_probs': crop_result.pop('_general_probs'),
                            'embedding': crop_result.pop('_embedding', None),
                            'ccip': None,
                        })
                    except Exception:
                        continue
                r = {'crops': crops, 'boxes': [list(b) for b in boxes]}
                proposed_regions = _derive_proposed_regions([(0, r)], item_chars, tagger_backend, image_indices=[resolved_index])
            except Exception:
                logging.exception('detect_regions: character matching failed')

        return Response({
            'image_index': resolved_index,
            'boxes': [list(box) for box in boxes],
            'proposed_regions': proposed_regions,
        })

    @action(detail=True, methods=['post'], url_path='character_regions')
    def character_regions_view(self, request, pk=None):
        """Save human-assigned region↔character labels across ALL of this
        item's images at once — see Item.character_regions (models.py) and
        RegionAnnotator.jsx's "保存" button.

        Body: `{regions: [{image_index: int|null, box:[x1,y1,x2,y2],
        characters:[str, ...]}, ...]}`. Each region carries its own
        image_index (rather than one shared for the whole request) since a
        single save can include boxes drawn on several different images of
        the item. `characters` is a list, not one name — a box sometimes
        needs more than one label (e.g. person-detection merged two
        overlapping people into a single box) — see the field's own
        docstring in models.py for how training consumes multi-character
        regions differently from single-character ones.

        Any character name used here that isn't already in item.characters
        is added — labeling a region is itself a confident statement that
        this character appears in the image, same trust level as picking it
        from CharacterPicker's free-text "add new" option.
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        regions = data.get('regions')
        if not isinstance(regions, list):
            return Response({'detail': 'regions must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        cleaned = []
        for r in regions:
            if not isinstance(r, dict):
                return Response({'detail': 'each region must be an object'}, status=status.HTTP_400_BAD_REQUEST)

            box = r.get('box')
            if (not isinstance(box, list) or len(box) != 4
                    or not all(isinstance(v, (int, float)) for v in box)):
                return Response({'detail': 'each region.box must be [x1, y1, x2, y2]'}, status=status.HTTP_400_BAD_REQUEST)

            characters = r.get('characters')
            if not isinstance(characters, list):
                return Response({'detail': 'each region.characters must be a list'}, status=status.HTTP_400_BAD_REQUEST)
            names = [c.strip() for c in characters if isinstance(c, str) and c.strip()]
            names = list(dict.fromkeys(names))  # de-dupe, preserve order
            if not names:
                return Response({'detail': 'each region must have at least one character'}, status=status.HTTP_400_BAD_REQUEST)

            image_index = r.get('image_index')
            if image_index is not None:
                try:
                    image_index = int(image_index)
                except (TypeError, ValueError):
                    return Response({'detail': 'region.image_index must be an integer or null'}, status=status.HTTP_400_BAD_REQUEST)

            cleaned.append({'image_index': image_index, 'box': [int(v) for v in box], 'characters': names})

        item.character_regions = cleaned

        existing_chars = list(item.characters or [])
        introduced_chars = []
        for r in cleaned:
            for name in r['characters']:
                if name not in existing_chars:
                    existing_chars.append(name)
                    introduced_chars.append(name)
        item.characters = existing_chars

        try:
            item.save(update_fields=['character_regions', 'characters'])
        except Exception as e:
            logging.exception('Failed to save character_regions for item %s', item.pk)
            return Response({'detail': 'Failed to save', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        grouping = _group_response_fields(ensure_characters_grouped(item, only=introduced_chars))
        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'saved', 'item': serializer.data, **grouping})

    @action(detail=False, methods=['get'], url_path='twitter_auth_check')
    def twitter_auth_check(self, request):
        """Twitter認証情報の有効性を確認する診断エンドポイント。

        resolve_own_account()経由(twid Cookie必須) — 古いverify_credentials()
        (twitter.com の v1.1 API)は2026年9月頃からHTTP 404を返すようになり
        使えなくなったため切り替えた(resolve_own_accountの docstring参照)。
        """
        if not HAVE_TWITTER_GQL:
            return Response({'ok': False, 'reason': 'twitter_gql_fetch module not available'})
        from .twitter_gql_fetch import resolve_own_account
        result = resolve_own_account()
        return Response(result)

    @action(detail=True, methods=['get', 'post'], url_path='translate-image')
    def translate_image(self, request, pk=None):
        """Translate in-image non-Japanese text to Japanese via Gemini 1.5 Flash.

        Query params / JSON body:
            index: int (0-based PreviewImage order, default 0 or best)
            force: bool (if True, ignore cached results and re-run Gemini)
        """
        import hashlib
        from . import gemini_creds
        from .image_translator import translate_image_with_gemini
        from .models import ItemImageTranslationCache

        api_key = gemini_creds.get_api_key()
        if not api_key:
            return Response(
                {
                    'detail': 'Gemini APIキーが設定されていません。ヘッダーメニューの「画像翻訳 (Gemini) 設定」からAPIキーを登録してください。',
                    'code': 'NO_API_KEY'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        item = self.get_object()

        # Parse index from the JSON body OR the query string: the frontend POSTs
        # with ?index=N, which the old body-only check ignored for POST — every
        # translation then ran on (and was cached as) image 0, so all pages of
        # a multi-image item showed the first page's result.
        idx_raw = request.data.get('index') if request.method == 'POST' else None
        if idx_raw is None:
            idx_raw = request.query_params.get('index')
        try:
            image_index = int(idx_raw) if idx_raw is not None else 0
        except (ValueError, TypeError):
            image_index = 0

        # Accept `force` from either the JSON body or the query string: the
        # frontend's re-translate button POSTs with ?force=true, which the old
        # body-only check ignored (so re-translate kept returning the cache).
        force_raw = request.data.get('force') if request.method == 'POST' else None
        if force_raw is None:
            force_raw = request.query_params.get('force', '')
        force = str(force_raw).lower() in ('true', '1', 'yes')

        image_bytes, resolved_index = _select_image_bytes(item, image_index)
        if not image_bytes:
            return Response({'detail': '画像が見つかりません。'}, status=status.HTTP_404_NOT_FOUND)

        actual_index = resolved_index if resolved_index is not None else 0
        img_hash = hashlib.sha256(image_bytes).hexdigest()

        # Check cache unless force
        if not force:
            cache = ItemImageTranslationCache.objects.filter(item=item, image_index=actual_index).first()
            if cache and cache.image_hash == img_hash:
                return Response({
                    'item_id': item.id,
                    'image_index': actual_index,
                    'cached': True,
                    'translations': cache.translations,
                })

        # GET is a cache lookup only (the preview pane calls it every time an
        # image is shown); actually running Gemini requires an explicit POST.
        if request.method != 'POST':
            return Response({
                'item_id': item.id,
                'image_index': actual_index,
                'cached': False,
                'translations': None,
            })

        # Run translation via Gemini
        try:
            translations, meta = translate_image_with_gemini(image_bytes, api_key, return_meta=True)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Update cache. An empty result is not cached: it is far more often a
        # recognition miss than a truly text-free image, and caching it would
        # keep returning "no text" until the user explicitly forces a re-run.
        if translations:
            ItemImageTranslationCache.objects.update_or_create(
                item=item,
                image_index=actual_index,
                defaults={
                    'image_hash': img_hash,
                    'translations': translations,
                }
            )
        else:
            ItemImageTranslationCache.objects.filter(item=item, image_index=actual_index).delete()

        return Response({
            'item_id': item.id,
            'image_index': actual_index,
            'cached': False,
            'translations': translations,
            'model': meta.get('model'),
            'elapsed_ms': meta.get('elapsed_ms'),
        })


def items_from_db(request):
    """Return all items serialized from the Django DB.

    This replaces the older `items_from_rust` name and endpoint.
    """
    qs = Item.objects.all().order_by('-id')
    serializer = ItemSerializer(qs, many=True, context={'request': request})
    return JsonResponse(serializer.data, safe=False)


class CharacterGroupViewSet(viewsets.ModelViewSet):
    # CharacterGroup is a small reference/lookup dataset (a classification
    # bucket per title/genre/brand — see the model's own docstring), not a
    # growing log like Item, which legitimately needs paging. Without this,
    # the list endpoint silently inherited the project's global DRF
    # pagination (PAGE_SIZE=50, see backend.pagination) — past 50 groups,
    # anything sorting alphabetically after the 50th (Meta.ordering =
    # ['name']) was dropped from page 1 and neither frontend consumer
    # (CharacterGroupManager.jsx / CharacterPicker.jsx) ever fetched a
    # further page, so those groups silently stopped appearing anywhere in
    # the UI — reported as "existing groups disappear once many groups
    # exist". Disabling pagination here always returns the complete list;
    # at the scale this app's own character/title vocabulary actually
    # reaches (tens to low hundreds of groups), that's a small enough
    # payload that paging it would only add complexity for no real benefit.
    pagination_class = None
    queryset = CharacterGroup.objects.all()
    serializer_class = CharacterGroupSerializer

    @action(detail=False, methods=['post'], url_path='move_character')
    def move_character(self, request):
        """Move a character name from one group to another (or to ungrouped).

        Body: { "character": "name", "from_group_id": 1|null, "to_group_id": 2|null }
        """
        char = (request.data.get('character') or '').strip()
        if not char:
            return Response({'detail': 'character required'}, status=status.HTTP_400_BAD_REQUEST)
        from_id = request.data.get('from_group_id')
        to_id = request.data.get('to_group_id')

        if from_id is not None:
            try:
                src = CharacterGroup.objects.get(pk=from_id)
                if char in src.characters:
                    src.characters = [c for c in src.characters if c != char]
                    src.save(update_fields=['characters'])
            except CharacterGroup.DoesNotExist:
                pass

        if to_id is not None:
            try:
                dst = CharacterGroup.objects.get(pk=to_id)
                if char not in dst.characters:
                    dst.characters = list(dst.characters) + [char]
                    dst.save(update_fields=['characters'])
            except CharacterGroup.DoesNotExist:
                return Response({'detail': 'target group not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response({'status': 'ok'})

    @action(detail=False, methods=['get'], url_path='character_usage_counts')
    def character_usage_counts(self, request):
        """{character_name: count} across every Item.characters in the DB —
        lets CharacterGroupManager.jsx show, next to each character chip,
        whether it's actually still used by anything (and if not, a human
        can confidently remove it from its group instead of guessing).
        Counts every character name that appears on any item, not just
        ones already in a CharacterGroup — a name absent from this dict
        entirely is exactly as "unused" as one present with count 0, but
        the frontend only ever looks this up for names it already has
        (group members), so the difference is moot there.

        Same full-scan-a-JSONField-in-Python pattern used throughout this
        file (e.g. _suggest_from_existing_data, CharacterDanbooruLinkViewSet.
        list) rather than a DB-side aggregate — Item.characters is a
        JSONField list, which neither SQLite nor a portable ORM query can
        group/count elements of directly.
        """
        counts = Counter()
        for chars in Item.objects.exclude(characters=[]).exclude(characters__isnull=True).values_list(
            'characters', flat=True,
        ):
            counts.update(c for c in (chars or []) if c)
        return Response(dict(counts))


class CharacterAliasGroupViewSet(viewsets.ModelViewSet):
    """Same "small reference dataset, no pagination" pattern as
    CharacterGroupViewSet just above (see that class's own comment) — a
    row here is a human-confirmed (or human-rejected) CharacterAliasGroup;
    see that model's own docstring for what it means and how it differs
    from CharacterGroup. Normal create/update/destroy handle both deciding
    a candidate (POST {characters, linked}) and undoing a past decision
    (DELETE); the `candidates` action below is the only custom piece.
    """
    pagination_class = None
    queryset = CharacterAliasGroup.objects.all().order_by('-created_at')
    serializer_class = CharacterAliasGroupSerializer

    @action(detail=False, methods=['get'])
    def candidates(self, request):
        """Distinct character-name sets (2+ names) that a human put on the
        SAME Item.character_regions box somewhere in the DB, excluding any
        set already reviewed (linked or rejected) via this same ViewSet —
        the pending "are these the same person?" queue for
        CharacterAliasGroupManager.jsx. Mined from actual labeling data
        rather than guessed from name similarity, since the only real
        signal for "these names are the same identity" this app has is a
        human having already put both on one box.

        Small-scale full scan (same reasoning as region_mismatch_queue's
        own docstring: the JSON-list "is this exactly that other list"
        comparison isn't expressible as a single portable DB query, and the
        candidate set — items with any character_regions at all — is
        already small at this app's scale). No before_id paging: unlike
        the item queues, distinct name-SETS are the rows here, and there
        are far fewer of those than items.
        """
        reviewed = {tuple(sorted(set(g.characters))) for g in CharacterAliasGroup.objects.all()}
        counts = defaultdict(int)
        examples = defaultdict(list)
        for item in Item.objects.exclude(character_regions=[]).only('id', 'character_regions').iterator():
            for region in (item.character_regions or []):
                names = region.get('characters') or []
                key = tuple(sorted(set(names)))
                if len(key) < 2:
                    continue
                counts[key] += 1
                if len(examples[key]) < 5:
                    examples[key].append(item.id)

        results = [
            {'characters': list(key), 'count': n, 'example_item_ids': examples[key]}
            for key, n in counts.items()
            if key not in reviewed
        ]
        results.sort(key=lambda r: -r['count'])
        return Response({'results': results})


class CharacterDanbooruLinkViewSet(viewsets.ViewSet):
    """Frontend-facing counterpart to item.management.commands.
    link_danbooru_characters and the one-off interactive review artifact
    used earlier to bulk-review its first run — makes the same "is this
    character name linked to a real Danbooru tag, and if not/wrongly, fix
    it" workflow a permanent part of the app instead of a one-time offline
    job + throwaway review tool.

    Not a ModelViewSet: `list` needs to show every character name this
    app's own Items actually use, INCLUDING ones link_danbooru_characters
    has never even attempted yet (no CharacterDanbooruLink row at all) —
    a plain queryset over CharacterDanbooruLink alone would silently hide
    exactly the characters a human most needs to review.
    """

    def list(self, request):
        titles_by_char = defaultdict(set)
        for item in Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'characters', 'titles',
        ).iterator():
            chars = [c for c in (item.characters or []) if c]
            titles = [t for t in (item.titles or []) if t]
            for c in chars:
                titles_by_char[c].update(titles)

        existing = {link.character_name: link for link in CharacterDanbooruLink.objects.all()}

        # Which CharacterGroup (if any) each character belongs to, and that
        # group's own parent group (e.g. "Fate/strange Fake" under "Fate")
        # -- shown alongside the link so a character sharing a bare name
        # with an unrelated one in a different series (or just an
        # unfamiliar name) can be told apart at a glance, instead of only
        # ever seeing whichever raw Item.titles the character happened to
        # appear under.
        group_by_char = {}
        for g in CharacterGroup.objects.select_related('parent').all():
            for c in (g.characters or []):
                group_by_char.setdefault(c, g)

        results = []
        for name in sorted(titles_by_char):
            link = existing.get(name)
            group = group_by_char.get(name)
            results.append({
                'character_name': name,
                'titles': sorted(titles_by_char[name]),
                'group_name': group.name if group else None,
                'parent_group_name': group.parent.name if group and group.parent else None,
                # False = link_danbooru_characters/the resolve action has
                # never even run for this name yet — distinct from "ran,
                # found nothing" (attempted=True, danbooru_tag=None).
                'attempted': link is not None,
                'danbooru_tag': link.danbooru_tag if link else None,
                'resolved_via': link.resolved_via if link else '',
                'match_score': link.match_score if link else None,
                'debug_info': link.debug_info if link else None,
                'updated_at': link.updated_at if link else None,
                'conflict_resolved': link.conflict_resolved if link else False,
            })
        return Response(results)

    @action(detail=False, methods=['post'], url_path='mark_conflict_resolved')
    def mark_conflict_resolved(self, request):
        """Body: {"character_name": "...", "resolved": true|false (default true)}.

        Manual override for a row danbooru_lookup.dedupe_tag_collisions
        demoted (danbooru_tag cleared due to a collision with another
        character's tag) whose actual fix was renaming/merging THIS
        character's own name elsewhere in the app (Item.characters/
        CharacterGroup) to match the tag's rightful owner, rather than
        ever finding it a different Danbooru tag of its own — there is no
        tag to link once that's done, so the row would otherwise sit in
        the "unresolved" tab forever despite a human already having fully
        handled it. See CharacterDanbooruLink.conflict_resolved's own
        docstring; CharacterDanbooruLinkManager.jsx's classify() treats a
        row with this set the same as a real link regardless of
        danbooru_tag.
        """
        name = (request.data.get('character_name') or '').strip()
        if not name:
            return Response({'detail': 'character_name required'}, status=status.HTTP_400_BAD_REQUEST)
        resolved = bool(request.data.get('resolved', True))

        link, _created = CharacterDanbooruLink.objects.update_or_create(
            character_name=name,
            defaults={'conflict_resolved': resolved},
        )
        return Response({
            'character_name': link.character_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'conflict_resolved': link.conflict_resolved,
        })

    @action(detail=False, methods=['post'], url_path='resolve')
    def resolve(self, request):
        """Body: {"character_name": "..."}. Live Danbooru re-resolve for
        ONE character (a handful of HTTP requests to Danbooru's public
        API — the same per-character cost link_danbooru_characters pays,
        just triggered on demand instead of batched offline). Also
        re-checks for cross-character tag collisions since a fresh
        resolution can newly collide with an existing link — if THIS
        character loses that check, the response still reflects the
        post-collision state, not the momentarily-resolved one.
        """
        name = (request.data.get('character_name') or '').strip()
        if not name:
            return Response({'detail': 'character_name required'}, status=status.HTTP_400_BAD_REQUEST)

        link = danbooru_lookup.resolve_character_link(name)
        demotions = danbooru_lookup.dedupe_tag_collisions()
        link.refresh_from_db()
        return Response({
            'character_name': link.character_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'demotions': demotions,
        })

    @action(detail=False, methods=['get'], url_path='autocomplete')
    def autocomplete(self, request):
        """?q=... — live Danbooru tag-search suggestions (see
        danbooru_lookup.autocomplete_tags) for the manual-entry UI, so a
        human picks a real candidate tag instead of typing one from memory.
        """
        q = (request.GET.get('q') or '').strip()
        if not q:
            return Response([])
        return Response(danbooru_lookup.autocomplete_tags(q))

    @action(detail=False, methods=['get'], url_path='alias_search')
    def alias_search(self, request):
        """?q=... — Danbooru wiki pages whose other_names match `q` (see
        danbooru_lookup.search_aliases), for when the manual-entry UI's
        plain tag-name autocomplete comes up empty because the query is a
        katakana/Japanese name rather than the tag's own romanized form.
        """
        q = (request.GET.get('q') or '').strip()
        if not q:
            return Response([])
        return Response(danbooru_lookup.search_aliases(q))

    @action(detail=False, methods=['post'], url_path='manual')
    def manual(self, request):
        """Body: {"character_name": "...", "danbooru_tag": "..."|null}.
        Human override, mirroring how the earlier interactive review
        artifact's decisions were applied to this same table. A null/
        empty danbooru_tag means "confirmed no match" (a rejection), not
        "not yet attempted" — this still writes a CharacterDanbooruLink
        row so the character stops showing up as unattempted.

        A non-empty tag is validated against Danbooru's own API first
        (danbooru_lookup.tag_exists) — a manually-typed tag is much more
        likely to be a typo than a real new alias, and a wrong link here
        would be trusted at suggestion time exactly like an automated one
        (see views._match_tagger_characters), so it gets the same
        "never store a guess" treatment as the automated path.
        """
        name = (request.data.get('character_name') or '').strip()
        if not name:
            return Response({'detail': 'character_name required'}, status=status.HTTP_400_BAD_REQUEST)
        tag = (request.data.get('danbooru_tag') or '').strip() or None

        if tag and not danbooru_lookup.tag_exists(tag):
            return Response(
                {'detail': f'"{tag}" does not appear to be a real Danbooru tag — check spelling.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link, _ = CharacterDanbooruLink.objects.update_or_create(
            character_name=name,
            defaults={
                'danbooru_tag': tag,
                'resolved_via': 'human_review',
                'match_score': 1.0 if tag else None,
                'debug_info': {'reason': 'manually set' if tag else 'manually rejected — no match'},
            },
        )
        demotions = danbooru_lookup.dedupe_tag_collisions() if tag else []
        link.refresh_from_db()
        return Response({
            'character_name': link.character_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'demotions': demotions,
        })


class TitleDanbooruLinkViewSet(viewsets.ViewSet):
    """Title-side counterpart to CharacterDanbooruLinkViewSet — same
    review workflow (list every title actually used in the DB, whether
    or not it's been resolved yet; resolve one at a time against
    Danbooru's copyright-category tags; manual override; mark a tag-
    collision demotion resolved once its real fix — renaming/merging the
    title elsewhere — has been done by hand), just keyed by title name
    instead of character name. See TitleDanbooruLink's own model
    docstring and danbooru_lookup.resolve_title_link/dedupe_title_tag_
    collisions for the title-specific resolution logic."""

    def list(self, request):
        known_titles = set()
        for titles in Item.objects.exclude(titles=[]).exclude(titles__isnull=True).values_list('titles', flat=True):
            known_titles.update(t for t in (titles or []) if t)

        existing = {link.title_name: link for link in TitleDanbooruLink.objects.all()}

        results = []
        for name in sorted(known_titles):
            link = existing.get(name)
            results.append({
                'title_name': name,
                # False = resolve/manual has never even run for this title
                # yet — distinct from "ran, found nothing" (attempted=True,
                # danbooru_tag=None). Same convention as CharacterDanbooru
                # LinkViewSet.list's own 'attempted' field.
                'attempted': link is not None,
                'danbooru_tag': link.danbooru_tag if link else None,
                'resolved_via': link.resolved_via if link else '',
                'match_score': link.match_score if link else None,
                'debug_info': link.debug_info if link else None,
                'updated_at': link.updated_at if link else None,
                'conflict_resolved': link.conflict_resolved if link else False,
            })
        return Response(results)

    @action(detail=False, methods=['post'], url_path='resolve')
    def resolve(self, request):
        """Body: {"title_name": "..."}. Live Danbooru re-resolve for ONE
        title. Also re-checks for cross-title tag collisions since a
        fresh resolution can newly collide with an existing link."""
        name = (request.data.get('title_name') or '').strip()
        if not name:
            return Response({'detail': 'title_name required'}, status=status.HTTP_400_BAD_REQUEST)

        link = danbooru_lookup.resolve_title_link(name)
        demotions = danbooru_lookup.dedupe_title_tag_collisions()
        link.refresh_from_db()
        return Response({
            'title_name': link.title_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'demotions': demotions,
        })

    @action(detail=False, methods=['get'], url_path='autocomplete')
    def autocomplete(self, request):
        """?q=... — live Danbooru tag-search suggestions (see
        danbooru_lookup.autocomplete_tags) for the manual-entry UI. Not
        filtered to copyright-only here — the UI itself shows each
        candidate's category (see CharacterDanbooruLinkManager.jsx's own
        CATEGORY_COLORS) so a human can tell at a glance if a suggestion
        is actually a series tag or something else."""
        q = (request.GET.get('q') or '').strip()
        if not q:
            return Response([])
        return Response(danbooru_lookup.autocomplete_tags(q))

    @action(detail=False, methods=['get'], url_path='alias_search')
    def alias_search(self, request):
        """?q=... — Danbooru wiki pages whose other_names match `q` (see
        danbooru_lookup.search_aliases), for a title whose Danbooru tag
        name doesn't obviously match its Japanese/English display name.
        """
        q = (request.GET.get('q') or '').strip()
        if not q:
            return Response([])
        return Response(danbooru_lookup.search_aliases(q))

    @action(detail=False, methods=['post'], url_path='manual')
    def manual(self, request):
        """Body: {"title_name": "...", "danbooru_tag": "..."|null}. Human
        override — same "never store an unvalidated guess" treatment as
        CharacterDanbooruLinkViewSet.manual: a non-empty tag is checked
        against Danbooru's own API first."""
        name = (request.data.get('title_name') or '').strip()
        if not name:
            return Response({'detail': 'title_name required'}, status=status.HTTP_400_BAD_REQUEST)
        tag = (request.data.get('danbooru_tag') or '').strip() or None

        if tag and not danbooru_lookup.tag_exists(tag):
            return Response(
                {'detail': f'"{tag}" does not appear to be a real Danbooru tag — check spelling.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link, _ = TitleDanbooruLink.objects.update_or_create(
            title_name=name,
            defaults={
                'danbooru_tag': tag,
                'resolved_via': 'human_review',
                'match_score': 1.0 if tag else None,
                'debug_info': {'reason': 'manually set' if tag else 'manually rejected — no match'},
            },
        )
        demotions = danbooru_lookup.dedupe_title_tag_collisions() if tag else []
        link.refresh_from_db()
        return Response({
            'title_name': link.title_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'demotions': demotions,
        })

    @action(detail=False, methods=['post'], url_path='mark_conflict_resolved')
    def mark_conflict_resolved(self, request):
        """Body: {"title_name": "...", "resolved": true|false (default
        true)}. Same manual override as CharacterDanbooruLinkViewSet.
        mark_conflict_resolved, for a title-side tag collision whose real
        fix was renaming/merging the title elsewhere rather than finding
        it a different tag."""
        name = (request.data.get('title_name') or '').strip()
        if not name:
            return Response({'detail': 'title_name required'}, status=status.HTTP_400_BAD_REQUEST)
        resolved = bool(request.data.get('resolved', True))

        link, _created = TitleDanbooruLink.objects.update_or_create(
            title_name=name,
            defaults={'conflict_resolved': resolved},
        )
        return Response({
            'title_name': link.title_name,
            'danbooru_tag': link.danbooru_tag,
            'resolved_via': link.resolved_via,
            'match_score': link.match_score,
            'debug_info': link.debug_info,
            'conflict_resolved': link.conflict_resolved,
        })

