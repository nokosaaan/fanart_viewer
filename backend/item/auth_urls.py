from django.urls import path
from .auth_views import login_view

urlpatterns = [
    path('', login_view),
]
