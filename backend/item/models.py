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
    # Set once manage.py backfill_descriptions has confirmed this item's
    # description status via a successful API call — regardless of whether
    # text was actually found (a genuinely textless/deleted/inaccessible
    # tweet still counts as "checked"). Lets that command's queryset skip
    # already-checked items on a re-run instead of re-querying Twitter for
    # the same empty-description items every time; left null after a failed
    # attempt (network/auth error) so those DO get retried later, since the
    # failure there was ours, not a fact about the tweet.
    description_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    preview_data = models.BinaryField(null=True, blank=True)
    preview_content_type = models.CharField(max_length=100, null=True, blank=True)
    # Human-assigned ground-truth region labels for multi-character images —
    # one entry per box, across ALL of this item's preview images (not just
    # one — an item can have several fetched images, and each may have its
    # own multi-character content worth labeling):
    #   [{"image_index": int|None, "box": [x1,y1,x2,y2],
    #     "characters": ["name", ...]}, ...]
    # `image_index` is 0-based into item.preview_images ordered by `order`
    # (same indexing as ItemViewSet.preview's own ?index=N) — None only for
    # the legacy single-image fallback (no PreviewImage rows at all, just
    # the denormalized preview_data field — see _select_image_bytes).
    # `box` is in that one image's absolute pixel coordinates (same tuple
    # format as tagger._detect_person_boxes, so a region here feeds
    # tagger._crop_with_padding directly, no translation needed).
    # `characters` is a list, not a single name, because person-detection
    # sometimes merges two overlapping people (e.g. a hug pose) into one
    # box — see item.management.commands.train_character_classifier's
    # _get_manual_labeled_rows, which only ever treats a region as a clean
    # single-identity training example when it has EXACTLY one character;
    # 2+ names on one box are recorded (useful metadata) but skipped for
    # classifier training since the crop's identity is inherently ambiguous.
    character_regions = models.JSONField(default=list, blank=True)
    # Content fingerprint (see views._char_diff_signature) of (region-
    # derived characters, item.characters) at the moment a human last
    # explicitly reviewed a region_mismatch_queue conflict on this item and
    # decided item.characters is fine as-is (views.acknowledge_character_
    # mismatch) — WITHOUT changing either side's data (that's what
    # sync_characters_to_regions is for instead: it makes them equal for
    # real, so nothing needs remembering). Lets region_mismatch_queue stop
    # re-flagging an accepted "some confirmed character just has no box
    # yet, and that's fine" state on every visit — content-addressed rather
    # than a plain boolean/timestamp so it self-invalidates the moment
    # EITHER side changes again (the freshly computed signature just won't
    # match this stored one anymore), with no extra bookkeeping needed on
    # every write path that touches characters or character_regions.
    character_regions_ack_signature = models.CharField(max_length=64, blank=True, default='')

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
    # Optional third cookie (`twid`, format "u=<numeric user id>") — X sets
    # this for every logged-in session, and it's the only reliable
    # "who am I" signal poll_twitter_updates.py's own discovery has left
    # after https://twitter.com/i/api/1.1/account/verify_credentials.json
    # (the old REST endpoint it used to resolve this) started returning
    # HTTP 404 — see item.twitter_gql_fetch.resolve_own_account, which
    # parses the numeric id out of this instead of calling that endpoint at
    # all. Optional (blank means poll_twitter_updates.py's Likes discovery
    # just can't run — Bookmarks discovery doesn't need this) since a
    # deployment set up before this existed still has auth_token/ct0 only.
    encrypted_twid = models.BinaryField(null=True, blank=True)
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
    # Post text, already extracted from the Bookmarks/Likes GraphQL response
    # during discovery (see twitter_gql_fetch._extract_full_text) — carried
    # through to Item creation in poll_twitter_updates._drain_one so it
    # isn't thrown away and re-derived (unreliably — see
    # ItemViewSet.fetch_and_save_preview's own description-backfill step,
    # which only ever runs as a fallback) by the later fetch_and_save_preview
    # call.
    description = models.TextField(blank=True, default='')
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
    screen_name — twitter_gql_fetch.resolve_own_account()の結果をキャッシュ
    したもの。毎tickで呼ぶとレート制限の消費が増えるため、未設定または
    直近で認証エラーが起きた時だけ再解決する。
    """
    screen_name = models.CharField(max_length=64, blank=True, default='')
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)
    # Pagination cursor to resume each timeline's discovery scan from on the
    # NEXT tick, instead of always restarting from the newest — set only
    # when a tick's scan used up its whole max_pages budget without ever
    # reaching an already-known tweet (see twitter_gql_fetch._fetch_social_
    # timeline's own docstring for the full reasoning: without this, a
    # backlog bigger than one page — e.g. after this poller was unable to
    # run for a while — could never be fully discovered automatically,
    # since every tick would restart at the top and immediately re-hit the
    # now-known items from the previous tick). Cleared back to '' once a
    # scan actually reaches a known tweet or the true end of the timeline
    # (i.e. genuinely caught up) — at that point restarting from the top
    # next time is correct again.
    bookmarks_resume_cursor = models.CharField(max_length=255, blank=True, default='')
    likes_resume_cursor = models.CharField(max_length=255, blank=True, default='')

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


class DanbooruAliasCache(models.Model):
    """Caches hashtag-text -> this app's own already-registered character
    name, resolved via Danbooru's wiki page `other_names` aliases (see
    item.danbooru_lookup.find_registered_character_via_alias). Bridges a
    hashtag written in a different script than however this app's own
    vocabulary happens to have that same character registered — e.g. a
    katakana hashtag ("キュアエクレール") when the app's own Item.characters
    already has the romaji form ("cure eclair") registered, or the reverse.
    `resolved_character_name` is null when Danbooru had no matching alias
    (also cached, to avoid re-querying a hashtag that will never resolve —
    most hashtags are just spoiler/series tags, not character aliases).
    """
    hashtag_norm = models.CharField(max_length=200, unique=True)
    resolved_character_name = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.hashtag_norm} -> {self.resolved_character_name or '(no match)'}"


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


class CharacterAliasGroup(models.Model):
    """A set of character names a human confirmed all refer to the SAME
    identity — e.g. a magical girl's real name and her transformed name.
    NOT the same thing as CharacterGroup (that's a franchise/genre
    classification bucket of otherwise-distinct characters; see its own
    docstring for the exact "not a per-character alias list" distinction).

    `characters` is always stored sorted (see CharacterAliasGroupSerializer)
    so exact-set matching (both here and in
    train_character_classifier._get_manual_labeled_rows /
    views._expand_character_alias) can compare simple sorted-list equality.

    Rows are discovered, not typed in from scratch: a person labeling
    Item.character_regions sometimes puts 2+ names on one box because both
    names are valid for that one person, not because two different people
    got merged into a single detected box (see character_regions_view's own
    docstring on that ambiguity). ItemViewSet -> CharacterAliasGroupViewSet.
    candidates mines every such multi-name box across all items into
    candidate sets, and a human decides per candidate via
    CharacterAliasGroupManager.jsx whether to confirm it (`linked=True` —
    now usable for training + inference alias expansion) or reject it
    (`linked=False` — a tombstone so the same candidate set doesn't keep
    resurfacing as a candidate; still excluded from `candidates` output but
    never used by training/inference).
    """
    characters = models.JSONField(default=list)
    linked = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['characters'], name='unique_character_alias_group_characters'),
        ]

    def __str__(self):
        return ' = '.join(self.characters)
