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
    from .twitter_gql_fetch import fetch_twitter_media, TwitterAuthError
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


def _normalize_char_name(name):
    return re.sub(r'\s+', ' ', (name or '').strip().lower())


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
            existing_by_norm.setdefault(_normalize_char_name(c), []).append((c, group))
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
    empty = {'titles': [], 'characters': [], 'situation_hint': None, 'sample_size': 0}
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
    suggested_characters = [c for c, n in char_counter.most_common(10) if n >= min_count]
    top_situation = situation_counter.most_common(1)
    suggested_situation = top_situation[0][0] if top_situation and top_situation[0][1] >= min_count else None

    return {
        'titles': suggested_titles,
        'characters': suggested_characters,
        'situation_hint': suggested_situation,
        'sample_size': len(siblings),
    }


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
            if not candidates and HAVE_GALLERYDL and os.environ.get('TWITTER_AUTH_TOKEN'):
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        gdl_results = fetch_twitter_media_gallerydl(target_url)
                        for (img_bytes, mime) in gdl_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'gallerydl'
                    except Exception:
                        logging.exception('gallery-dl fetch failed for %s', target_url)

            # GraphQL API fallback (browser-free, but more fragile than gallery-dl).
            if not candidates and HAVE_TWITTER_GQL and os.environ.get('TWITTER_AUTH_TOKEN'):
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        gql_results = fetch_twitter_media(target_url)
                        for (img_bytes, mime) in gql_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'twitter_gql'
                    except TwitterAuthError as e:
                        logging.warning('Twitter GQL auth error for %s: %s', target_url, e)
                    except Exception:
                        logging.exception('Twitter GQL fetch failed for %s', target_url)

            # yt-dlp fallback (primarily video, last resort for images).
            if not candidates and HAVE_YTDLP and os.environ.get('TWITTER_AUTH_TOKEN'):
                if ('twitter.com' in target_url) or ('x.com' in target_url):
                    try:
                        ytdlp_results = fetch_twitter_media_ytdlp(target_url)
                        for (img_bytes, mime) in ytdlp_results:
                            if img_bytes and len(img_bytes) >= MIN_IMAGE_FETCH_BYTES:
                                candidates.append((target_url, img_bytes, mime))
                                used_method = 'ytdlp'
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
        titles,characters,tags,situation. Defaults to all four.
        """
        valid_fields = ('titles', 'characters', 'tags', 'situation')
        requested = (request.GET.get('missing') or ','.join(valid_fields)).split(',')
        fields = [f.strip() for f in requested if f.strip() in valid_fields] or list(valid_fields)

        q = Q()
        for f in fields:
            if f == 'situation':
                q |= Q(situation='') | Q(situation__isnull=True)
            else:
                # JSONField list (titles/characters/tags): empty means [] or null
                q |= Q(**{f: []}) | Q(**{f'{f}__isnull': True})

        queryset = Item.objects.filter(q).order_by('-id')
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='suggest_tags')
    def suggest_tags_view(self, request, pk=None):
        """Suggest titles/characters/tags/situation for this item.

        Primary source: this item's artist's OTHER already-tagged items —
        a pure DB lookup (see _suggest_from_existing_data), near-instant,
        no image analysis. The image tagger only runs as a fallback when
        that yields too little (no titles or no characters), since it's a
        ~5s+ CPU-bound operation per image and the DB signal, when it's
        available at all, is usually both faster and more precise (it's
        drawn from what this user already curated, not a model's guess).

        Nothing here is written to the Item — saving still goes through
        update_fields as normal.
        """
        item = self.get_object()
        db = _suggest_from_existing_data(item)

        titles = list(db['titles'])
        characters = [{'name': c, 'score': None, 'matched': True, 'source': 'db'} for c in db['characters']]
        tags = []
        situation_hint = db['situation_hint']
        source = 'db' if (titles or characters) else 'none'

        needs_tagger = not titles or not characters
        if needs_tagger and HAVE_TAGGER:
            imgs = list(item.preview_images.order_by('order'))
            if imgs:
                image_bytes = bytes(max(imgs, key=lambda x: len(x.data or b'')).data)
            elif item.preview_data:
                image_bytes = bytes(item.preview_data)
            else:
                image_bytes = None

            if image_bytes is not None:
                data = request.data if isinstance(request.data, dict) else {}
                try:
                    general_threshold = float(data.get('general_threshold', 0.35))
                    character_threshold = float(data.get('character_threshold', 0.85))
                except (TypeError, ValueError):
                    general_threshold, character_threshold = 0.35, 0.85

                try:
                    tagger_result = tagger.suggest_tags(
                        image_bytes,
                        general_threshold=general_threshold,
                        character_threshold=character_threshold,
                    )
                except Exception:
                    logging.exception('Tagger inference failed for item %s', item.id)
                    tagger_result = None

                if tagger_result is not None:
                    source = 'tagger' if source == 'none' else 'db+tagger'
                    if not characters:
                        matched_chars, tagger_titles = _match_tagger_characters(tagger_result['characters'])
                        characters = matched_chars
                        for t in tagger_titles:
                            if t not in titles:
                                titles.append(t)
                    tags = tagger_result['tags']
                    if not situation_hint:
                        situation_hint = tagger_result['situation_hint']

        return Response({
            'characters': characters,
            'tags': tags,
            'situation_hint': situation_hint,
            'suggested_titles': titles,
            'source': source,
            'sample_size': db['sample_size'],
        })

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

