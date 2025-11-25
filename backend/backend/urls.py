from django.urls import path, include
from django.contrib import admin
from django.http import JsonResponse


def _health(request):
    return JsonResponse({'ok': True, 'service': 'fanart-backend'})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/_health/', _health),
    path('api/', include('item.urls')),
]
