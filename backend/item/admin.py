from django.contrib import admin
from .models import Item, SocialFetchQueueItem, TwitterPollState


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('external_id', 'artist', 'situation')
    search_fields = ('artist',)


@admin.register(SocialFetchQueueItem)
class SocialFetchQueueItemAdmin(admin.ModelAdmin):
    list_display = ('platform', 'kind', 'external_id', 'screen_name', 'status', 'created_at')
    list_filter = ('platform', 'kind', 'status')


@admin.register(TwitterPollState)
class TwitterPollStateAdmin(admin.ModelAdmin):
    list_display = ('screen_name', 'last_success_at', 'consecutive_failures', 'last_error_at')
