from django.urls import path
from .auth_views import login_view, admin_login_view

urlpatterns = [
    path('', login_view),
    path('admin/', admin_login_view),
]
