"""Cleaned views for the `item` app.

This module exposes a conservative `ItemViewSet` and a Python-native
`items_from_db` view that returns serialized items from the Django DB. The
older "from_rust" wording has been removed.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.utils import timezone
from collections import Counter, defaultdict
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from .models import Item, PreviewImage, CharacterGroup, CharacterDanbooruLink, CharacterAliasGroup, SocialFetchQueueItem, TwitterPollState
from .twitter_creds import has_credentials as _have_twitter_creds
from . import danbooru_lookup
from . import pixiv_salvage
from .danbooru_lookup import resolve_title_from_character as _resolve_title_from_character
from .serializers import ItemSerializer, CharacterGroupSerializer, CharacterAliasGroupSerializer
from security.ssrf_guard import validate_url, SSRFError
import logging
import traceback
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


def _match_hashtags(description, external=False):
    """Direct match of hashtags from the source post's own text (see
    Item.description) against the app's existing title/character
    vocabulary. This is the single most reliable signal available when
    present — the artist's own naming, not an inference — so it's tried
    first, independently of (and before) any image analysis.

    Same normalized-string-match limitation as _match_tagger_characters:
    only catches hashtags that already spell a title/character the same
    way something in this app's vocabulary does — this app's own vocabulary
    mixes Japanese and romaji names across different characters (whichever
    form was registered for each), and a hashtag can just as easily be
    written in the OTHER script than however that particular character
    happens to be registered (e.g. hashtag "キュアエクレール" vs a
    registered "cure eclair", or the reverse).

    `external`: when true, any hashtag that doesn't match directly also
    gets a Danbooru wiki-alias lookup (see
    danbooru_lookup.find_registered_character_via_alias) — the one network
    call in this function, so gated behind the same explicit opt-in as the
    rest of this pipeline's Danbooru use (see suggest_tags_view's
    `external` flag). Capped at _HASHTAG_ALIAS_LOOKUP_CAP per call and
    cached per hashtag text (DanbooruAliasCache), so this cost is paid at
    most once per hashtag ever seen, not once per suggestion run.
    """
    hashtags = _extract_hashtags(description)
    if not hashtags:
        return {'titles': [], 'characters': []}
    normalized_hashtags = {_normalize_char_name(h) for h in hashtags}

    title_by_norm = {}
    for titles in Item.objects.exclude(titles=[]).values_list('titles', flat=True):
        for name in (titles or []):
            if name:
                title_by_norm.setdefault(_normalize_char_name(name), name)

    char_by_norm = {}
    all_char_names = []
    for group in CharacterGroup.objects.all():
        for c in (group.characters or []):
            char_by_norm.setdefault(_normalize_char_name(c), c)
            all_char_names.append(c)
    for chars in Item.objects.exclude(characters=[]).values_list('characters', flat=True):
        for c in (chars or []):
            if c:
                char_by_norm.setdefault(_normalize_char_name(c), c)
                all_char_names.append(c)

    matched_titles = {title_by_norm[h] for h in normalized_hashtags if h in title_by_norm}
    matched_chars = {char_by_norm[h] for h in normalized_hashtags if h in char_by_norm}

    if external:
        unmatched = [h for h in hashtags if _normalize_char_name(h) not in char_by_norm]
        for h in unmatched[:_HASHTAG_ALIAS_LOOKUP_CAP]:
            try:
                bridged = danbooru_lookup.find_registered_character_via_alias(h, all_char_names)
            except Exception:
                logging.exception('Danbooru alias lookup failed for hashtag %r', h)
                continue
            if bridged:
                matched_chars.add(bridged)

    return {
        'titles': sorted(matched_titles),
        'characters': sorted(matched_chars),
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


def _tag_weight(tag):
    words = tag.split()
    if len(words) >= 2 and words[0] in _COLOR_WORDS and words[-1] in ('eyes', 'hair'):
        return TAG_WEIGHT_FEATURE
    if any(kw in tag for kw in _ACCESSORY_KEYWORDS):
        return TAG_WEIGHT_FEATURE
    return TAG_WEIGHT_GENERIC


def _weighted_tag_signature(tags, description):
    """{tag_or_hashtag: weight} for one item, for scoring similarity
    against another item's signature."""
    sig = {}
    for t in (tags or []):
        if t:
            sig[t] = max(sig.get(t, 0), _tag_weight(t))
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


def _suggest_from_similar_tags(tag_names, description, exclude_pk):
    """Suggest titles/characters/situation from OTHER items whose (weighted)
    tags overlap substantially with this item's. Complements
    _suggest_from_existing_data's artist-based prior: an artist who draws
    many different things won't get a useful suggestion from "their other
    items", but items sharing a specific combination of visual tags
    plausibly depict the same character/series regardless of who drew them.

    Returns titles/title_candidates split: `titles` only holds candidates
    that cleared the normal confidence bar; when NONE do but there was
    still some signal, `title_candidates` holds a short, unranked-confidence
    list instead of nothing — better to let the user pick from a couple of
    plausible options than silently give up.
    """
    empty = {'titles': [], 'title_candidates': [], 'characters': [], 'situation_hint': None, 'sample_size': 0}
    query_sig = _weighted_tag_signature(tag_names, description)
    plain_tags = [t for t in (tag_names or []) if t]
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
        sib_sig = _weighted_tag_signature(c.tags, c.description)
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
                    if use_classifier and want_characters and not characters \
                            and tagger_result.get('person_count', 0) <= 1:
                        char_name, confidence = tagger.predict_character(
                            tagger_result['general_probs'], tagger_backend,
                        )
                        if char_name:
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

    return {
        'characters': characters,
        'tags': tags,
        'situation_hint': situation_hint,
        'suggested_titles': titles,
        'title_candidates': title_candidates,
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
                         tag_limit_for_matching=None, image_index=None):
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

    if want_titles or want_characters:
        hashtag_hits = _match_hashtags(item.description, external=external)
        if want_titles:
            for t in hashtag_hits['titles']:
                title_c.append({'value': t, 'source': 'hashtag', 'confidence': 1.0})
        if want_characters:
            for c in hashtag_hits['characters']:
                char_c.append({'value': c, 'source': 'hashtag', 'confidence': 1.0})

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
        sim = _suggest_from_similar_tags(item.tags or [], item.description, exclude_pk=item.pk)
        _append_tag_similarity_candidates(sim, title_c, char_c, situation_c,
                                           want_titles, want_characters, want_situation)

    tagger_result = None
    resolved_image_index = None
    if (want_titles or want_characters or want_tags or want_situation) and HAVE_TAGGER:
        image_bytes, resolved_image_index = _select_image_bytes(item, image_index)

        if image_bytes is not None:
            try:
                tagger_result = tagger.suggest_tags(
                    image_bytes,
                    general_threshold=general_threshold,
                    character_threshold=character_threshold,
                    backend=tagger_backend,
                )
            except Exception:
                logging.exception('Tagger inference failed for item %s', item.id)

    if tagger_result is not None:
        if want_tags:
            tags_out = tagger_result['tags']
        if want_situation and tagger_result['situation_hint']:
            situation_c.append({'value': tagger_result['situation_hint'], 'source': 'tagger', 'confidence': 1.0})

        if want_characters or want_titles:
            matched_chars, tagger_titles = _match_tagger_characters(tagger_result['characters'])
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
                    char_c.append({
                        'value': c['name'], 'source': 'tagger', 'confidence': confidence,
                        'raw_name': c.get('raw_name'), 'match_method': c.get('match_method'),
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
            # vocabulary). Only trusted on single-subject images
            # (person_count <= 1) since it was only ever trained on those —
            # a multi-character image's blended features would be a
            # meaningless extrapolation for it, not a real prediction.
            if want_characters and tagger_result.get('person_count', 0) <= 1:
                char_name, confidence = tagger.predict_character(
                    tagger_result['general_probs'], tagger_backend,
                )
                if char_name:
                    for n in _expand_character_alias(char_name):
                        char_c.append({'value': n, 'source': 'classifier', 'confidence': confidence})

        if want_titles or want_characters or want_situation:
            tags_for_matching = tagger_result['tags_full']
            if tag_limit_for_matching is not None:
                tags_for_matching = tags_for_matching[:tag_limit_for_matching]
            sim2 = _suggest_from_similar_tags(tags_for_matching, item.description, exclude_pk=item.pk)
            _append_tag_similarity_candidates(sim2, title_c, char_c, situation_c,
                                               want_titles, want_characters, want_situation)

    return {
        'title': title_c, 'character': char_c, 'situation': situation_c, 'tags': tags_out,
        'want_titles': want_titles, 'want_characters': want_characters,
        'want_tags': want_tags, 'want_situation': want_situation,
        'image_index': resolved_image_index,
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
}

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
            })
    return breakdown


def _suggest_for_item_ensemble(item, external=False, tagger_backend='onnx',
                                general_threshold=0.35, character_threshold=0.85,
                                image_index=None):
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
        image_index=image_index,
    )

    title_values, title_scores = _combine_candidates(collected['title'], DEFAULT_ENSEMBLE_WEIGHTS, top_k=3)
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
    char_values, char_scores = _combine_candidates(collected['character'], DEFAULT_ENSEMBLE_WEIGHTS, top_k=3)
    situation_values, _situation_scores = _combine_candidates(collected['situation'], DEFAULT_SITUATION_WEIGHTS, top_k=1)

    char_breakdown = _character_breakdown(collected['character'], char_values)
    characters = [
        {
            'name': name, 'score': round(char_scores[name], 4), 'matched': True, 'source': 'ensemble',
            'contributors': char_breakdown[name],
        }
        for name in char_values
    ]

    title_breakdown = _character_breakdown(collected['title'], title_values)
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

    return {
        'characters': characters,
        'titles': titles,
        'tags': collected['tags'],
        'situation_hint': situation_values[0] if situation_values else None,
        'suggested_titles': [],
        'title_candidates': [],
        'source': 'ensemble',
        'sample_size': 0,
        'image_index': collected['image_index'],
    }


def _known_titles_and_characters_excluding(item_pk):
    """All titles/characters used by every OTHER Item — one combined scan
    (mirrors ItemViewSet.all_titles/all_characters, but both fields at once
    and excluding one item) so _maybe_autocreate_character_group can tell
    whether a title/character this save introduces is genuinely new to the
    whole app, not just new to the one Item being edited."""
    titles, characters = set(), set()
    for other in Item.objects.exclude(pk=item_pk).only('titles', 'characters').iterator():
        for t in (other.titles or []):
            if t and isinstance(t, str):
                titles.add(t.strip())
        for c in (other.characters or []):
            if c and isinstance(c, str):
                characters.add(c.strip())
    return titles, characters


def _maybe_autocreate_character_group(item, orig_titles, orig_characters):
    """Editing an item to give it BOTH a title and character(s) that have
    never appeared anywhere else in the app is exactly the moment a new
    series first gets archived — the same moment a human would otherwise
    have to remember to go create a matching CharacterGroup by hand (see
    CharacterGroup's own docstring: it's what scopes character-name
    suggestions/matching to the right title, and what
    train_character_classifier's title-roster resolution keys off). Auto-
    creates one instead: named after the new title, seeded with every
    character on this item (not just the newly-typed ones — for a
    brand-new title, everyone appearing on this first item legitimately
    belongs to it, even a name that happens to already exist elsewhere for
    an unrelated franchise) and pre-linked to that title (CharacterGroup.
    titles) so it shows up for this title immediately, without a human
    needing to separately open the character-group manager and link it.

    Deliberately narrow: only fires when THIS save introduces a title that
    has never been used by any other Item AND at least one character that
    has never been used by any other Item, in the same call — reusing an
    existing title (even to add a brand-new character to it) is a
    judgment call about whether that character belongs in an existing
    group or a new one, which is left to a human via
    CharacterGroupManager.jsx rather than guessed at here. Never touches
    an existing CharacterGroup — get_or_create only creates; if a group
    with this exact name coincidentally already exists (e.g. two different
    items introducing the "same" new title back to back), its existing
    characters/titles are extended, never overwritten.

    Returns the CharacterGroup it created/extended, or None if the "new
    title + new character" condition wasn't met.
    """
    new_titles = [t for t in (item.titles or []) if t and t not in orig_titles]
    new_characters = [c for c in (item.characters or []) if c and c not in orig_characters]
    if not new_titles or not new_characters:
        return None

    known_titles, known_characters = _known_titles_and_characters_excluding(item.pk)
    brand_new_titles = [t for t in new_titles if t not in known_titles]
    brand_new_characters = [c for c in new_characters if c not in known_characters]
    if not brand_new_titles or not brand_new_characters:
        return None

    name = brand_new_titles[0]
    group, _created = CharacterGroup.objects.get_or_create(name=name, defaults={'characters': [], 'titles': []})
    group.characters = _merge_unique(group.characters, item.characters or [])
    group.titles = _merge_unique(group.titles, brand_new_titles)
    group.save(update_fields=['characters', 'titles'])
    return group


def _maybe_assign_new_characters_to_existing_groups(item, orig_characters):
    """The everyday counterpart to _maybe_autocreate_character_group: an
    ALREADY-established title (one an existing CharacterGroup already
    claims via its own `titles`) getting a brand-new character doesn't
    need a new group — the character just belongs in the one that's
    already there. Without this, every such addition would otherwise have
    to be assigned by hand via CharacterGroupManager.jsx's own
    "未分類"(unclassified) checkbox list, every single time.

    Same "never guess" posture as _maybe_autocreate_character_group:
      - Only characters this save actually introduces are considered —
        one already on the item before this save is left wherever it
        already is.
      - A character already belonging to ANY existing group (this one or
        a different one) is left alone. It may deliberately live in a
        broader/different group on purpose (e.g. a franchise-wide
        character kept on a parent group rather than a title-specific
        child one — see CharacterGroup.parent's own docstring), so
        silently moving it here would undo a human's earlier, deliberate
        placement.
      - Only fires when this item's titles resolve to EXACTLY ONE distinct
        CharacterGroup across all of them (via an exact match against that
        group's own `titles` list, not a fuzzy one) — a crossover item
        whose two titles belong to two different groups is a genuine
        ambiguity for a human to resolve, not something to guess at here.

    Returns the CharacterGroup characters were added to, or None if
    nothing qualified (including the case where _maybe_autocreate_
    character_group already handled this exact character via a brand-new
    group of its own — by the time this runs, that character is no longer
    ungrouped, so it's correctly skipped here instead of double-handled).
    """
    new_characters = [c for c in (item.characters or []) if c and c not in orig_characters]
    titles = [t for t in (item.titles or []) if t]
    if not new_characters or not titles:
        return None

    matching_groups = {}
    already_grouped = set()
    for g in CharacterGroup.objects.all():
        already_grouped.update(g.characters or [])
        if any(t in (g.titles or []) for t in titles):
            matching_groups[g.pk] = g
    if len(matching_groups) != 1:
        return None
    group = next(iter(matching_groups.values()))

    to_add = [c for c in new_characters if c not in already_grouped]
    if not to_add:
        return None

    group.characters = _merge_unique(group.characters, to_add)
    group.save(update_fields=['characters'])
    return group


class ItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Item viewset exposing read-only item list/retrieve and minimal preview endpoints."""
    queryset = Item.objects.all().order_by('-id')
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

            # Poipiku: dedicated fetcher that handles IllustItemThubExpand and
            # the ShowAppendFile AJAX endpoint.  Run for any poipiku.com URL,
            # regardless of whether HTML scraping found something, because the
            # generic scraper only picks up the first thumbnail at _640 size.
            if HAVE_POIPIKU and 'poipiku.com' in target_url:
                try:
                    poipiku_results = fetch_poipiku_media(target_url)
                    if poipiku_results:
                        candidates = []  # replace generic results with poipiku-specific ones
                        for (img_bytes, mime) in poipiku_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                        if candidates:
                            used_method = 'poipiku'
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
            data.append({'id': img.id, 'index': idx, 'url': f"/api/items/{item.id}/previews/{idx}/", 'content_type': img.content_type})
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
            pi.delete()
            # re-order remaining preview images to keep contiguous order
            remaining = list(item.preview_images.order_by('order'))
            for new_idx, img in enumerate(remaining):
                if img.order != new_idx:
                    img.order = new_idx
                    img.save()
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
                # delete the targeted preview image
                target = imgs[idxi]
                target.delete()
                # re-order remaining preview images to keep contiguous order
                remaining = list(item.preview_images.order_by('order'))
                for new_idx, img in enumerate(remaining):
                    if img.order != new_idx:
                        img.order = new_idx
                        img.save()
                return Response({'status': 'deleted', 'index': idxi})
            except Exception as e:
                logging.exception('Failed to delete preview image')
                return Response({'detail': 'Failed to delete preview', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # GET: return the image bytes for the requested index
        img = imgs[idxi]
        return HttpResponse(img.data, content_type=img.content_type or 'application/octet-stream')

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

        Excludes situation SOLO (only one person — nothing to disambiguate)
        and R18 (per explicit user request — kept out of this queue for
        now), and anything with no image to annotate at all.
        """
        queryset = (
            Item.objects.exclude(situation__in=['SOLO', 'R18'])
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
        item.characters = region_chars
        item.character_regions_ack_signature = ''  # now genuinely equal — nothing left to remember
        item.save(update_fields=['characters', 'character_regions_ack_signature'])
        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'saved', 'item': serializer.data})

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

        suggest_fn = _suggest_for_item_ensemble if use_ensemble else _suggest_for_item
        result = suggest_fn(
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
        # Snapshot BEFORE any field below gets applied — _maybe_autocreate_character_group
        # (called after save()) needs to know what was new to THIS item, not
        # just what ended up on it.
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

        auto_group = None
        if 'titles' in updates and 'characters' in updates:
            auto_group = _maybe_autocreate_character_group(item, orig_titles, orig_characters)

        assigned_group = None
        if 'characters' in updates:
            assigned_group = _maybe_assign_new_characters_to_existing_groups(item, orig_characters)

        serializer = ItemSerializer(item, context={'request': request})
        response = {'status': 'updated', 'updated': updates, 'item': serializer.data}
        if auto_group is not None:
            response['auto_created_character_group'] = {'id': auto_group.id, 'name': auto_group.name}
        if assigned_group is not None:
            response['auto_assigned_to_character_group'] = {'id': assigned_group.id, 'name': assigned_group.name}
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
                PreviewImage.objects.create(item=item, order=idx, data=f.read(), content_type=f.content_type or 'image/jpeg')
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
        return Response({
            'image_index': resolved_index,
            'boxes': [list(box) for box in boxes],
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
        for r in cleaned:
            for name in r['characters']:
                if name not in existing_chars:
                    existing_chars.append(name)
        item.characters = existing_chars

        try:
            item.save(update_fields=['character_regions', 'characters'])
        except Exception as e:
            logging.exception('Failed to save character_regions for item %s', item.pk)
            return Response({'detail': 'Failed to save', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'saved', 'item': serializer.data})

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

        results = []
        for name in sorted(titles_by_char):
            link = existing.get(name)
            results.append({
                'character_name': name,
                'titles': sorted(titles_by_char[name]),
                # False = link_danbooru_characters/the resolve action has
                # never even run for this name yet — distinct from "ran,
                # found nothing" (attempted=True, danbooru_tag=None).
                'attempted': link is not None,
                'danbooru_tag': link.danbooru_tag if link else None,
                'resolved_via': link.resolved_via if link else '',
                'match_score': link.match_score if link else None,
                'debug_info': link.debug_info if link else None,
                'updated_at': link.updated_at if link else None,
            })
        return Response(results)

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

