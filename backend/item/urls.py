from rest_framework.routers import DefaultRouter
from .views import ItemViewSet, CharacterGroupViewSet, CharacterDanbooruLinkViewSet, CharacterAliasGroupViewSet
from .views import items_from_db
from .backup_views import backup_create_view, backup_list_view, backup_restore_view
from .twitter_creds_views import twitter_creds_status_view, twitter_creds_set_view
from .pixiv_creds_views import pixiv_creds_status_view, pixiv_creds_set_view
from .poipiku_creds_views import poipiku_creds_status_view, poipiku_creds_set_view
from .twitter_poll_views import twitter_poll_status_view
from .pixiv_poll_views import pixiv_poll_status_view
from .poipiku_poll_views import poipiku_poll_status_view
from .poller_settings_views import poller_settings_status_view, poller_settings_set_view
from .drive_creds_views import drive_creds_status_view, drive_creds_set_view, drive_creds_authenticate_view
from .train_classifier_views import train_classifier_status_view, train_classifier_start_view
from django.urls import path, include

router = DefaultRouter()
router.register(r'items', ItemViewSet, basename='item')
router.register(r'character-groups', CharacterGroupViewSet, basename='character-group')
router.register(r'character-links', CharacterDanbooruLinkViewSet, basename='character-link')
router.register(r'character-alias-groups', CharacterAliasGroupViewSet, basename='character-alias-group')

urlpatterns = [
    path('', include(router.urls)),
    path('items_from_db/', items_from_db),
    path('backup/create/', backup_create_view),
    path('backup/list/', backup_list_view),
    path('backup/restore/', backup_restore_view),
    path('twitter_creds/status/', twitter_creds_status_view),
    path('twitter_creds/set/', twitter_creds_set_view),
    path('pixiv_creds/status/', pixiv_creds_status_view),
    path('pixiv_creds/set/', pixiv_creds_set_view),
    path('poipiku_creds/status/', poipiku_creds_status_view),
    path('poipiku_creds/set/', poipiku_creds_set_view),
    path('twitter_poll/status/', twitter_poll_status_view),
    path('pixiv_poll/status/', pixiv_poll_status_view),
    path('poipiku_poll/status/', poipiku_poll_status_view),
    path('poller_settings/<str:platform>/status/', poller_settings_status_view),
    path('poller_settings/<str:platform>/set/', poller_settings_set_view),
    path('drive_creds/status/', drive_creds_status_view),
    path('drive_creds/set/', drive_creds_set_view),
    path('drive_creds/authenticate/', drive_creds_authenticate_view),
    path('train_classifier/status/', train_classifier_status_view),
    path('train_classifier/start/', train_classifier_start_view),
]
