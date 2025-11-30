"""Cleaned views for the `item` app.

This module exposes a conservative `ItemViewSet` and a Python-native
`items_from_db` view that returns serialized items from the Django DB. The
older "from_rust" wording has been removed.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import HttpResponse, JsonResponse
import re
from urllib.parse import urljoin, urlparse

from .models import Item, PreviewImage
from .serializers import ItemSerializer
from django.conf import settings
import logging
import traceback
import base64
from .utils import fetch_twitter_media_urls, fetch_twitter_media_urls_with_sources, get_last_api_response
import os
from .headless_fetch import fetch_rendered_media
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.management import call_command
import io
import tempfile
import traceback
try:
    from .playwright_helper import fetch_images_with_playwright
    HAVE_PIXIV_PLAYWRIGHT = True
except Exception:
    HAVE_PIXIV_PLAYWRIGHT = False
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


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


class ItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Item viewset exposing read-only item list/retrieve and minimal preview endpoints."""
    queryset = Item.objects.all().order_by('external_id')
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
        body, ctype = _internal_fetch(target_url)
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
                    if cand_url in seen:
                        continue
                    seen.add(cand_url)
                    b, ct = _internal_fetch(cand_url)
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
                            b, ct = _internal_fetch(tw_url)
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
                                    # Skip very small images (icons/UI assets)
                                    try:
                                        if len(body or b'') < 10240:
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
                                b, ct = _internal_fetch(h, min_size=10240)
                                if b and ct:
                                    candidates.append((h, b, ct))
                                    used_method = used_method or 'playwright'
                        except Exception:
                            continue

        if not candidates:
            return Response({'detail': 'No image candidates found or failed to fetch', 'hints': hints}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # If Playwright was explicitly requested, enforce a global minimum
        # size threshold to remove small icons/thumbnails that might have
        # been collected earlier via HTML scraping. This ensures the
        # Playwright path yields only substantial image candidates.
        try:
            if force_method == 'playwright':
                min_bytes = 10240
                filtered = []
                for (u, b, ct) in candidates:
                    try:
                        if b and len(b) >= min_bytes:
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
                saved.append({'index': idx, 'url': url_f, 'size': len(body) if body else 0, 'content_type': ctype})
            except Exception:
                # skip individual failures but continue saving others
                logging.exception('Failed to save preview candidate %s for item %s', url_f, item.id)
                continue

        if not saved:
            return Response({'detail': 'Failed to save any preview images'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'status': 'saved', 'count': len(saved), 'saved': saved})

    @action(detail=True, methods=['post'], url_path='save_previews')
    def save_previews(self, request, pk=None):
        """Accepts client-provided images (data_uri) and persists them as PreviewImage."""
        item = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        images = data.get('images') or []
        if not isinstance(images, list) or not images:
            return Response({'detail': 'No images provided'}, status=status.HTTP_400_BAD_REQUEST)
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
                    PreviewImage.objects.create(item=item, order=idx, data=body, content_type=ctype)
                    saved.append({'index': idx, 'url': url, 'size': len(body), 'content_type': ctype})
                except Exception:
                    continue
        if not saved:
            return Response({'detail': 'No images saved'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response({'status': 'saved', 'count': len(saved), 'saved': saved})

    @action(detail=True, methods=['get'], url_path='previews')
    def previews(self, request, pk=None):
        item = self.get_object()
        imgs = item.preview_images.order_by('order')
        data = []
        for idx, img in enumerate(imgs):
            data.append({'index': idx, 'url': f"/api/items/{item.id}/previews/{idx}/", 'content_type': img.content_type})
        return Response(data)

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


    @action(detail=True, methods=['post'], url_path='update_fields')
    def update_fields(self, request, pk=None):
        """Update editable JSON fields on an Item (characters, tags, titles).

        Expects JSON body with any of: `characters` (list), `tags` (list|null), `titles` (list).
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

        if not updates:
            return Response({'detail': 'No updatable fields provided'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item.save()
        except Exception as e:
            logging.exception('Failed to save Item updates')
            return Response({'detail': 'Failed to save', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = ItemSerializer(item, context={'request': request})
        return Response({'status': 'updated', 'updated': updates, 'item': serializer.data})


def items_from_db(request):
    """Return all items serialized from the Django DB.

    This replaces the older `items_from_rust` name and endpoint.
    """
    qs = Item.objects.all().order_by('external_id')
    serializer = ItemSerializer(qs, many=True, context={'request': request})
    return JsonResponse(serializer.data, safe=False)


@csrf_exempt
def restore_previews_upload(request):
    """Admin-only endpoint to upload a dumpdata JSON and run the
    `restore_previews_from_fixture` management command.

    Expects multipart/form-data with fields:
    - `file`: the JSON fixture file (required)
    - `password`: plain password to match env `RESTORE_PREVIEWS_PASSWORD` (required)
    - `dry_run`: optional; '1'/'true' to run in dry-run mode

    The function writes the uploaded file to a temporary path, calls the
    management command and returns the captured stdout/stderr as JSON.
    """
    try:
        # Basic CORS handling for preflight and responses. Normally
        # django-cors-headers handles this, but some proxies or setups
        # can short-circuit OPTIONS. Handle preflight here to be safe.
        origin = request.META.get('HTTP_ORIGIN')
        def set_cors(resp):
            try:
                if origin:
                    if getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False):
                        resp['Access-Control-Allow-Origin'] = '*'
                    else:
                        allowed = getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []
                        allowed_norm = [a.rstrip('/') for a in allowed]
                        if origin.rstrip('/') in allowed_norm:
                            resp['Access-Control-Allow-Origin'] = origin
                # safe defaults for the admin endpoint
                resp['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
                resp['Access-Control-Allow-Headers'] = 'Content-Type'
                resp['Access-Control-Max-Age'] = '3600'
            except Exception:
                pass
            return resp

        # Preflight
        if request.method == 'OPTIONS':
            from django.http import HttpResponse
            resp = HttpResponse(status=200)
            resp = set_cors(resp)
            return resp

        env_pw = os.environ.get('RESTORE_PREVIEWS_PASSWORD')
        provided = None
        try:
            provided = request.POST.get('password')
        except Exception:
            provided = None

        if not env_pw or (provided is None) or (provided != env_pw):
            resp = JsonResponse({'detail': 'Forbidden'}, status=403)
            return set_cors(resp)

        uploaded = request.FILES.get('file')
        if not uploaded:
            resp = JsonResponse({'detail': 'No file uploaded (field `file` required)'}, status=400)
            return set_cors(resp)

        dry = False
        try:
            dr = request.POST.get('dry_run')
            if dr and str(dr).lower() in ('1', 'true', 'on'):
                dry = True
        except Exception:
            dry = False

        # Accept raw JSON or compressed archives (.zip, .gz). For .zip, we
        # extract the first .json file found. We avoid loading the whole
        # content into memory by streaming where possible and writing to a
        # temporary file which is passed to the management command.
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        try:
            name = (uploaded.name or '').lower()
            # If ZIP archive
            if name.endswith('.zip'):
                import zipfile
                # zipfile accepts a file-like object
                try:
                    z = zipfile.ZipFile(uploaded.file)
                except Exception:
                    # fallback: read bytes from uploaded and open from bytes
                    uploaded.file.seek(0)
                    data = uploaded.file.read()
                    z = zipfile.ZipFile(io.BytesIO(data))

                # Find first candidate JSON file
                member = None
                for m in z.namelist():
                    if m.lower().endswith('.json'):
                        member = m
                        break
                if not member:
                    resp = JsonResponse({'detail': 'ZIP archive contains no .json file'}, status=400)
                    return set_cors(resp)

                # Protect against zip-slip by not using member paths directly
                try:
                    raw = z.read(member)
                except Exception as e:
                    resp = JsonResponse({'detail': f'Failed to read zip member: {e}'}, status=400)
                    return set_cors(resp)

                tmp.write(raw)
                tmp.flush()
                tmp.close()

            # If gzip (.gz)
            elif name.endswith('.gz') or name.endswith('.tgz'):
                import gzip
                try:
                    # uploaded.file is a file-like object; ensure at start
                    uploaded.file.seek(0)
                except Exception:
                    pass
                try:
                    with gzip.GzipFile(fileobj=uploaded.file, mode='rb') as g:
                        # stream read/write
                        while True:
                            chunk = g.read(1024 * 64)
                            if not chunk:
                                break
                            tmp.write(chunk)
                except Exception as e:
                    resp = JsonResponse({'detail': f'Failed to decompress gzip: {e}'}, status=400)
                    return set_cors(resp)
                tmp.flush()
                tmp.close()

            # Not compressed; assume raw JSON
            else:
                # Write uploaded file chunks to tmp
                try:
                    for chunk in uploaded.chunks():
                        tmp.write(chunk)
                except Exception:
                    # Fallback to reading fileobj
                    try:
                        uploaded.file.seek(0)
                        tmp.write(uploaded.file.read())
                    except Exception:
                        pass
                tmp.flush()
                tmp.close()

            # Call management command and capture output
            buf = io.StringIO()
            if dry:
                call_command('restore_previews_from_fixture', tmp.name, '--dry-run', stdout=buf, stderr=buf)
            else:
                call_command('restore_previews_from_fixture', tmp.name, stdout=buf, stderr=buf)
            out = buf.getvalue()
            resp = JsonResponse({'ok': True, 'output': out})
            return set_cors(resp)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    except Exception as e:
        tb = traceback.format_exc()
        resp = JsonResponse({'ok': False, 'error': str(e), 'trace': tb}, status=500)
        try:
            return set_cors(resp)
        except Exception:
            return resp

