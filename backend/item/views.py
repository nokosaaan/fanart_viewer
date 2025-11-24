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
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


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

        def _internal_fetch(url):
            headers = {'User-Agent': 'fanart-viewer-bot/1.0'}
            try:
                import requests as _requests
                r = _requests.get(url, timeout=15, headers=headers, allow_redirects=True)
                ct = r.headers.get('content-type', '')
                if r.status_code == 200 and ct and ct.split(';', 1)[0].startswith('image'):
                    return r.content, ct.split(';', 1)[0]
            except Exception:
                return None, None
            return None, None

        # Read client-selected fetch method early so we can honor it below
        force_method = data.get('force_method') if isinstance(data, dict) else None

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
                    root = soup.find('div', class_=lambda c: c and 'react-root' in c)
                    mains = []
                    if root:
                        mains = root.find_all('main')
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
                except Exception:
                    continue

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
                        return Response({'detail': 'API fetch returned no media for this tweet'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
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

        if not candidates:
            return Response({'detail': 'No image candidates found or failed to fetch', 'hints': hints}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

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
            if os.environ.get('TW_API_DEBUG'):
                api_debug = get_last_api_response(target_url)
                if api_debug is not None:
                    resp['api_response'] = api_debug
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

    @action(detail=True, methods=['get'], url_path='previews/(?P<idx>[^/]+)')
    def preview_index(self, request, pk=None, idx=None):
        item = self.get_object()
        try:
            idxi = int(idx)
        except Exception:
            return Response({'detail': 'invalid index'}, status=status.HTTP_400_BAD_REQUEST)
        imgs = list(item.preview_images.order_by('order'))
        if idxi < 0 or idxi >= len(imgs):
            return Response({'detail': 'index out of range'}, status=status.HTTP_404_NOT_FOUND)
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

