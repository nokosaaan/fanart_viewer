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
from collections import Counter
import re
from urllib.parse import urljoin, urlparse

from .models import Item, PreviewImage, CharacterGroup
from .twitter_creds import has_credentials as _have_twitter_creds
from .danbooru_lookup import resolve_title_from_character as _resolve_title_from_character
from .serializers import ItemSerializer, CharacterGroupSerializer
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
    from .twitter_gql_fetch import fetch_twitter_media, fetch_account_retweets, TwitterAuthError
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


def _run_bookmark_fetch_job(item_id, target_url, data=None):
    """Run the slow bookmark fetch/save flow outside the request thread."""
    try:
        request = SimpleNamespace(data=data or {}, query_params={})
        view = ItemViewSet()
        view.kwargs = {'pk': str(item_id)}
        view.fetch_and_save_preview(request, pk=item_id)
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
        outcome = _archive_retweet_candidate(rt)
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


def _archive_retweet_candidate(rt):
    """Create an Item (+ download its images) for one retweet candidate
    dict, as produced by twitter_gql_fetch.fetch_account_retweets:
    {'tweet_id', 'screen_name', 'media_urls', 'description'}.

    Shared by the "auto" background scan (_run_account_retweets_job) and the
    "manual review" import endpoint (import_retweets_view) so both paths
    dedupe/create/download identically.

    `screen_name` here is deliberately NEVER the scanned account — only the
    retweet's original author, or blank if that couldn't be read from the
    API response (Twitter occasionally omits it — see
    fetch_account_retweets' page-retry). A blank artist is left for the
    user to fill in later via the edit queue rather than silently guessing
    wrong (attributing the RT to whoever's timeline it came from would be
    incorrect and easy to miss).

    Returns 'created', 'skipped' (already archived), or 'failed'.
    """
    tweet_id = rt.get('tweet_id')
    author = rt.get('screen_name') or ''
    url = f'https://x.com/{author}/status/{tweet_id}' if author else f'https://x.com/i/status/{tweet_id}'

    if _find_item_by_url(url):
        return 'skipped'

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
        return 'failed'

    saved_any = False
    for idx, media_url in enumerate(rt.get('media_urls') or []):
        try:
            body, ctype = _fetch_image_via_requests(media_url, min_size=MIN_IMAGE_FETCH_BYTES)
            if body and ctype:
                PreviewImage.objects.create(item=item, order=idx, data=body, content_type=ctype)
                saved_any = True
        except Exception:
            logging.exception('Failed to download retweet media %s for tweet %s', media_url, tweet_id)

    if saved_any:
        return 'created'
    # Nothing downloadable (dead CDN link, transient error) — drop the
    # empty Item rather than leave a preview-less row behind.
    item.delete()
    return 'failed'


def _normalize_char_name(name):
    return re.sub(r'\s+', ' ', (name or '').strip().lower().replace('_', ' '))


def _match_tagger_characters(candidates):
    """Cross-reference raw tagger character-tag names (Danbooru-style,
    lowercase/space-separated English) against the app's own character
    vocabulary, so a match reuses the EXISTING spelling/casing (avoiding
    near-duplicate strings like "hakurei reimu" vs "Hakurei Reimu") and,
    when the match belongs to a CharacterGroup linked to one or more
    titles, surfaces those as title suggestions too — backfilling the
    tagger's inherent gap (its public tag list has no copyright/series
    tags at all, so it can never suggest a title on its own).

    Only catches same-spelling matches after normalization — it can't
    bridge e.g. a Japanese-named existing entry to the tagger's romaji
    output. `matched` on each returned item tells the caller which is which.
    """
    existing_by_norm = {}
    for group in CharacterGroup.objects.all():
        for c in (group.characters or []):
            # A group-alias match always resolves to the GROUP's own name,
            # never to whichever alias string happened to match — the alias
            # list exists purely to bridge recognition (e.g. tagger names a
            # character "sherry" while this app's own convention for that
            # character is the group's Japanese name "シェリー"), not to
            # decide what gets displayed as the suggestion.
            existing_by_norm.setdefault(_normalize_char_name(c), []).append((group.name, group))
    for item in Item.objects.only('characters'):
        for c in (item.characters or []):
            if c:
                existing_by_norm.setdefault(_normalize_char_name(c), []).append((c, None))

    matched, unmatched = [], []
    suggested_titles = set()
    for cand in candidates:
        hits = existing_by_norm.get(_normalize_char_name(cand['name']))
        if hits:
            existing_name, group = hits[0]
            matched.append({'name': existing_name, 'score': cand['score'], 'matched': True})
            if group:
                suggested_titles.update(group.titles or [])
        else:
            unmatched.append({'name': cand['name'], 'score': cand['score'], 'matched': False})

    return matched + unmatched, sorted(suggested_titles)


def _match_hashtags(description):
    """Direct match of hashtags from the source post's own text (see
    Item.description) against the app's existing title/character
    vocabulary. This is the single most reliable signal available when
    present — the artist's own naming, not an inference — so it's tried
    first, independently of (and before) any image analysis.

    Same normalized-string-match limitation as _match_tagger_characters:
    only catches hashtags that already spell a title/character the same
    way something in this app's vocabulary does.
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
    for group in CharacterGroup.objects.all():
        for c in (group.characters or []):
            char_by_norm.setdefault(_normalize_char_name(c), c)
    for chars in Item.objects.exclude(characters=[]).values_list('characters', flat=True):
        for c in (chars or []):
            if c:
                char_by_norm.setdefault(_normalize_char_name(c), c)

    return {
        'titles': sorted({title_by_norm[h] for h in normalized_hashtags if h in title_by_norm}),
        'characters': sorted({char_by_norm[h] for h in normalized_hashtags if h in char_by_norm}),
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

    q = Q()
    for t in plain_tags:
        q |= Q(tags__contains=[t])
    candidates = Item.objects.filter(q).exclude(pk=exclude_pk).only('tags', 'description', 'titles', 'characters', 'situation')

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


def _suggest_for_item(item, external=False, tagger_backend='onnx',
                       general_threshold=0.35, character_threshold=0.85,
                       tag_limit_for_matching=None):
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

    def _remaining():
        return (want_titles and not titles) or (want_characters and not characters) or (want_situation and not situation_hint)

    # Priority 1: hashtags straight from the source post's own text
    # (Item.description) — the artist's own words, not an inference.
    # Tried before anything else for exactly that reason. Counts as
    # 'db' in the `source` field returned below (same bucket as the
    # other no-image-analysis DB lookups — the frontend only
    # distinguishes "used the image model" from "didn't").
    if want_titles or want_characters:
        hashtag_hits = _match_hashtags(item.description)
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
        imgs = list(item.preview_images.order_by('order'))
        if imgs:
            image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
        elif item.preview_data:
            image_bytes = bytes(item.preview_data)
        else:
            image_bytes = None

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
                    characters = matched_chars
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
                         tag_limit_for_matching=None):
    """Research/evaluation counterpart to _suggest_for_item (see
    item.management.commands.evaluate_ensemble) — NOT wired into the live
    suggest_tags endpoint. _suggest_for_item stops at the first source that
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
        hashtag_hits = _match_hashtags(item.description)
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
    if (want_titles or want_characters or want_tags or want_situation) and HAVE_TAGGER:
        imgs = list(item.preview_images.order_by('order'))
        if imgs:
            image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
        elif item.preview_data:
            image_bytes = bytes(item.preview_data)
        else:
            image_bytes = None

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
                for c in matched_chars:
                    confidence = c['score'] if c.get('score') is not None else 0.5
                    char_c.append({'value': c['name'], 'source': 'tagger', 'confidence': confidence})
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
                    char_c.append({'value': char_name, 'source': 'classifier', 'confidence': confidence})

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
    }


# Starting weights, hand-tuned by rough source reliability (hashtags are the
# artist's own words; Danbooru reverse lookup is the model's own recognition
# corroborated by an authoritative external source; DB/tag-similarity fall
# in between) — meant to be swept/tuned against real confirmed-DB accuracy
# (see evaluate_ensemble --grid-search), not treated as final.
DEFAULT_ENSEMBLE_WEIGHTS = {
    'hashtag': 5.0,
    'artist_history': 2.0,
    'artist_history_weak': 1.0,
    'tag_similarity': 2.0,
    'tag_similarity_weak': 1.0,
    'tagger': 2.0,
    'tagger_group': 2.0,
    'danbooru': 3.0,
    'classifier': 3.0,
}

# Situation gets its OWN weight scheme, not DEFAULT_ENSEMBLE_WEIGHTS — the
# tagger's own composition/rating heuristic (see tagger._situation_hint:
# R18 from `rating` takes priority, then 1girl+solo -> SOLO, a 3+ count tag
# or "multiple girls" -> MULTIPLE) is a direct read of the image itself and
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

    @action(detail=True, methods=['post'], url_path='suggest_tags')
    def suggest_tags_view(self, request, pk=None):
        """Suggest titles/characters/tags/situation for this item — a thin
        HTTP wrapper around _suggest_for_item (module-level function below),
        which holds the actual logic and is also called directly by
        item.management.commands.evaluate_full_pipeline for offline accuracy
        evaluation against an in-memory (never-saved) item, without going
        through a live request.
        """
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        external = bool(data.get('external'))
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

        result = _suggest_for_item(
            item, external=external, tagger_backend=tagger_backend,
            general_threshold=general_threshold, character_threshold=character_threshold,
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

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'updated', 'updated': updates, 'item': serializer.data})

    @action(detail=False, methods=['get'], url_path='twitter_auth_check')
    def twitter_auth_check(self, request):
        """Twitter認証情報の有効性を確認する診断エンドポイント。"""
        if not HAVE_TWITTER_GQL:
            return Response({'ok': False, 'reason': 'twitter_gql_fetch module not available'})
        from .twitter_gql_fetch import verify_credentials
        result = verify_credentials()
        return Response(result)


def items_from_db(request):
    """Return all items serialized from the Django DB.

    This replaces the older `items_from_rust` name and endpoint.
    """
    qs = Item.objects.all().order_by('-id')
    serializer = ItemSerializer(qs, many=True, context={'request': request})
    return JsonResponse(serializer.data, safe=False)


class CharacterGroupViewSet(viewsets.ModelViewSet):
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

