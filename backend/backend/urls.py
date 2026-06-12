from django.urls import path, include, re_path
from django.contrib import admin
from django.conf import settings
from django.http import FileResponse, Http404
import os


def spa_fallback(request, path=''):
    """Serve index.html for any non-API path so React Router handles routing."""
    if settings.WHITENOISE_ROOT:
        index_path = os.path.join(settings.WHITENOISE_ROOT, 'index.html')
        if os.path.exists(index_path):
            return FileResponse(open(index_path, 'rb'), content_type='text/html')
    raise Http404


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('item.auth_urls')),
    path('api/', include('item.urls')),
    re_path(r'^.*$', spa_fallback),
]
