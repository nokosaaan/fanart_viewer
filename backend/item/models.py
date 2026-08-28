from django.db import models


class Item(models.Model):
    external_id = models.BigIntegerField()
    # source identifies which JSON/data source this record came from (e.g. 'manosaba', 'mygo')
    source = models.CharField(max_length=64, blank=True, default='')
    situation = models.CharField(max_length=64, blank=True)
    titles = models.JSONField(default=list, blank=True)
    characters = models.JSONField(default=list, blank=True)
    artist = models.CharField(max_length=255, blank=True)
    link = models.URLField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    # Raw post body/caption text from the source (Twitter/pixiv/poipiku),
    # captured alongside the image when the fetcher supports it. Hashtags in
    # here are the most reliable signal available for title/character
    # suggestion (see item.views._extract_hashtags) — more reliable than
    # any image-based inference, since they're the artist's own words.
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    preview_data = models.BinaryField(null=True, blank=True)
    preview_content_type = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.external_id} - {self.artist or 'unknown'}"


class PreviewImage(models.Model):
    item = models.ForeignKey(Item, related_name='preview_images', on_delete=models.CASCADE)
    order = models.IntegerField(default=0)
    data = models.BinaryField()
    content_type = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"PreviewImage {self.item_id}#{self.order} ({self.content_type})"


class TwitterCredential(models.Model):
    """Single-row store for the Twitter/X session cookies (auth_token, ct0)
    used by the scraping fetchers (see item.twitter_creds). Values are
    stored Fernet-encrypted (never plaintext) so that a DB-only leak (e.g.
    the Google Drive backup) doesn't expose usable session cookies — the
    decryption key lives only in TWITTER_CREDS_ENC_KEY, outside the DB.

    Never exposed via any API response — see item.twitter_creds_views,
    which only ever accepts new values (write-only), never returns them.
    """
    encrypted_auth_token = models.BinaryField(null=True, blank=True)
    encrypted_ct0 = models.BinaryField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"TwitterCredential(updated_at={self.updated_at})"


class DanbooruTitleCache(models.Model):
    """Caches character-tag -> series/title lookups against Danbooru's
    public API (see item.danbooru_lookup), so the same character is never
    looked up twice. `title` is null when Danbooru had no clear consensus
    (also cached, to avoid re-querying a character with no clean answer on
    every suggestion run).
    """
    character_tag = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.character_tag} -> {self.title or '(no match)'}"


class CharacterGroup(models.Model):
    name = models.CharField(max_length=200, unique=True)
    characters = models.JSONField(default=list, blank=True)
    # Freeform title strings this group belongs to (mirrors Item.titles —
    # there's no separate Title model, titles are just strings). Used to
    # restrict which groups are offered when editing an item with a given
    # title selected, so character-group naming doesn't drift independently
    # of title naming.
    titles = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
