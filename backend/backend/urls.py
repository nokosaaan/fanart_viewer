from django.urls import path, include
from django.contrib import admin

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('item.auth_urls')),
    path('api/', include('item.urls')),
]
