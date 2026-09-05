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


class SocialFetchQueueItem(models.Model):
    """FIFOキュー行1件 = ポーリングで見つかった、まだ取り込んでいない
    ブックマーク/いいね1件 (see item.management.commands.poll_twitter_updates).

    `external_id`はunique — 同じツイートがブックマークと「いいね」の
    両方で見つかっても行は1つだけ持つ(kindは最初に見つかった方を保持)。
    `created_at`(=挿入順=id順)がそのままFIFOの処理順になる: discoveryは
    新規発見分を古い順に反転してから投入するので、キュー全体を
    id昇順で辿ればブックマーク/いいねした順に近い形で処理できる。
    """
    PLATFORM_CHOICES = [('twitter', 'twitter')]
    KIND_CHOICES = [('bookmark', 'bookmark'), ('like', 'like')]
    STATUS_CHOICES = [
        ('pending', 'pending'),
        ('done', 'done'),
        ('skipped', 'skipped'),
        ('failed', 'failed'),
    ]

    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES, default='twitter')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    external_id = models.BigIntegerField(unique=True)
    screen_name = models.CharField(max_length=64, blank=True, default='')
    url = models.URLField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"SocialFetchQueueItem({self.platform}/{self.kind} {self.external_id} [{self.status}])"


class TwitterPollState(models.Model):
    """discoveryステップ(ブックマーク/いいねポーリング)の健康状態を保持する
    単一行。失効検知後の通知の重複送信を防ぐための状態もここに持つ
    (see item.notify.notify_discord, poll_twitter_updates)。

    `screen_name`はいいね一覧の取得に必要なログイン中アカウント自身の
    screen_name — verify_credentials()の結果をキャッシュしたもの。
    毎tickでverify_credentials()を呼ぶとレート制限の消費が増えるため、
    未設定または直近で認証エラーが起きた時だけ再解決する。
    """
    screen_name = models.CharField(max_length=64, blank=True, default='')
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)

    def __str__(self):
        return f"TwitterPollState(failures={self.consecutive_failures}, last_success={self.last_success_at})"


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


class CharacterDanbooruLink(models.Model):
    """Links this app's own character name (Japanese, as stored in
    Item.characters) to the matching Danbooru character tag (e.g.
    'hakurei_reimu'), so the tagger's OWN character-tag predictions — from
    a model trained on millions of Danbooru-tagged images, far more than
    this app's personal archive could ever provide per character — can be
    translated into a name that already exists in this DB instead of only
    ever matching by coincidental string equality (see
    views._match_tagger_characters, whose own docstring notes this exact
    gap: "it can't bridge e.g. a Japanese-named existing entry to the
    tagger's romaji output").

    Populated by management.commands.link_danbooru_characters, which uses
    item.danbooru_lookup.find_tag_via_title_roster (resolves each of the
    character's known titles to its Danbooru copyright wiki page, then
    fuzzy-matches the character's romanized name against that title's own
    cast roster) — never guessed at inference time. `danbooru_tag` is null
    when no confident match was found (also stored, so a character isn't
    re-queried against Danbooru's API on every run); `debug_info` keeps
    the per-title match-score detail for human review of a proposed link.
    """
    character_name = models.CharField(max_length=200, unique=True)
    danbooru_tag = models.CharField(max_length=200, null=True, blank=True)
    resolved_via = models.CharField(max_length=32, blank=True, default='')
    match_score = models.FloatField(null=True, blank=True)
    debug_info = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['danbooru_tag'])]

    def __str__(self):
        return f"{self.character_name} -> {self.danbooru_tag or '(unresolved)'}"


class CharacterGroup(models.Model):
    name = models.CharField(max_length=200, unique=True)
    characters = models.JSONField(default=list, blank=True)
    # Freeform title strings this group belongs to (mirrors Item.titles —
    # there's no separate Title model, titles are just strings). Used to
    # restrict which groups are offered when editing an item with a given
    # title selected, so character-group naming doesn't drift independently
    # of title naming.
    titles = models.JSONField(default=list, blank=True)
    # A CharacterGroup can itself belong to a broader one — mirrors
    # Danbooru's own wiki hierarchy (e.g. muv-luv -> muv-luv_girls_garden:
    # a franchise with narrower sub-titles under it). A character is
    # assigned directly to whichever group is actually specific enough for
    # it (a franchise-wide character like Illyasviel stays on the broad
    # "Fate" group; a character specific to one sub-title, like Francesca
    # in "Fate/strange Fake", is assigned to that child group instead) —
    # nothing forces every character down to a leaf. Self-referential and
    # nullable rather than a separate tree table since a group is already
    # its own natural tree node; SET_NULL on delete promotes any children
    # to top-level rather than cascading their deletion (deleting "Fate"
    # should not also delete "Fate/strange Fake").
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
