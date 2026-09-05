from rest_framework.routers import DefaultRouter
from .views import ItemViewSet, CharacterGroupViewSet, CharacterDanbooruLinkViewSet
from .views import items_from_db
from .backup_views import backup_create_view, backup_list_view, backup_restore_view
from .twitter_creds_views import twitter_creds_status_view, twitter_creds_set_view
from .twitter_poll_views import twitter_poll_status_view
from django.urls import path, include

router = DefaultRouter()
router.register(r'items', ItemViewSet, basename='item')
router.register(r'character-groups', CharacterGroupViewSet, basename='character-group')
router.register(r'character-links', CharacterDanbooruLinkViewSet, basename='character-link')

urlpatterns = [
    path('', include(router.urls)),
    path('items_from_db/', items_from_db),
    path('backup/create/', backup_create_view),
    path('backup/list/', backup_list_view),
    path('backup/restore/', backup_restore_view),
    path('twitter_creds/status/', twitter_creds_status_view),
    path('twitter_creds/set/', twitter_creds_set_view),
    path('twitter_poll/status/', twitter_poll_status_view),
]
