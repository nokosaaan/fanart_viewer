from rest_framework.routers import DefaultRouter
from .views import ItemViewSet, CharacterGroupViewSet
from .views import items_from_db
from django.urls import path, include

router = DefaultRouter()
router.register(r'items', ItemViewSet, basename='item')
router.register(r'character-groups', CharacterGroupViewSet, basename='character-group')

urlpatterns = [
    path('', include(router.urls)),
    path('items_from_db/', items_from_db),
]
