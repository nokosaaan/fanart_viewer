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
        if not item.link:
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

        # If the item.link itself points to an image, try that first
        body, ctype = _internal_fetch(item.link)
        candidates = []
        if body and ctype:
            candidates.append((item.link, body, ctype))
        else:
            # Fetch HTML and try to extract common image hints (og:image, twitter:image, img src)
            try:
                import requests
                r = requests.get(item.link, timeout=15, headers={'User-Agent': 'fanart-viewer-bot/1.0'})
                html = r.text or ''
            except Exception:
                html = ''

            # Look for Open Graph / Twitter / link rel tags and <img>
            hints = []
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

            # Resolve relative URLs and attempt fetches
            for h in hints:
                try:
                    cand_url = urljoin(item.link, h)
                    b, ct = _internal_fetch(cand_url)
                    if b and ct:
                        candidates.append((cand_url, b, ct))
                        break
                except Exception:
                    continue

        if not candidates:
            return Response({'detail': 'No image candidates found or failed to fetch'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Persist the first successful candidate as the primary preview
        PreviewImage.objects.filter(item=item).delete()
        url_f, body, ctype = candidates[0]
        pi = PreviewImage.objects.create(item=item, order=0, data=body, content_type=ctype)

        return Response({'status': 'saved', 'count': 1, 'url': url_f, 'size': len(body), 'content_type': ctype})

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

