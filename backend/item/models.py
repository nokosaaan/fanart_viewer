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


class PollerSettings(models.Model):
    """Per-platform on/off switch + rate controls for the background
    pollers (poll_twitter_updates / poll_pixiv_bookmarks) — see
    exe/launcher.py's per-platform poller threads. One row per `platform`
    ('twitter'/'pixiv'), not a single shared row — Twitter and Pixiv have
    genuinely independent rate-limit concerns and history sizes, so
    forcing them onto one shared enabled/interval made no sense once
    there were two real pollers to configure (they used to share a single
    row, back when this only had to cover Twitter).

    Defaults to enabled=True at the field level (preserves the existing
    docker `poller` service's always-on behavior for anyone already
    relying on it — it only ever checked has_credentials(), never asked
    permission); the exe-packaged build's launcher.py explicitly creates
    both platforms' rows with enabled=False on first run instead, since
    unattended background fetching without the user having opted in is
    exactly what a personal, per-user install shouldn't do silently.
    """
    PLATFORM_CHOICES = [('twitter', 'Twitter/X'), ('pixiv', 'Pixiv'), ('poipiku', 'Poipiku')]
    UNIT_CHOICES = [
        ('minutes', '分'), ('hours', '時間'), ('days', '日'), ('weeks', '週'),
    ]
    _UNIT_SECONDS = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, unique=True)
    enabled = models.BooleanField(default=True)
    # How many queued items this platform's poller _tick drains per tick
    # (previously hardcoded to exactly 1).
    items_per_tick = models.IntegerField(default=1)
    interval_value = models.IntegerField(default=6)
    interval_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='minutes')
    # How many discovery pages this platform's poller fetches per tick
    # during the initial backfill (before any history is known yet) --
    # previously hardcoded to MAX_PAGES_BACKFILL=3 in both commands.
    # Higher values reach the oldest bookmark/tweet sooner at the cost of
    # more requests per tick; has no effect once backfill is done
    # (steady-state discovery always uses just 1 page).
    backfill_pages_per_tick = models.IntegerField(default=3)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def interval_seconds(self) -> int:
        seconds = self.interval_value * self._UNIT_SECONDS.get(self.interval_unit, 60)
        return max(60, seconds)  # floor: never busy-loop on a misconfigured tiny value

    def __str__(self):
        return f"PollerSettings({self.platform}, enabled={self.enabled}, every {self.interval_value} {self.interval_unit})"


class PixivCredential(models.Model):
    """Single-row store for Pixiv login (see item.pixiv_creds). Mirrors
    TwitterCredential: Fernet-encrypted, decryption key lives only in
    PIXIV_CREDS_ENC_KEY outside the DB. Never exposed via any API
    response — see item.pixiv_creds_views (write-only).
    """
    encrypted_phpsessid = models.BinaryField(null=True, blank=True)
    encrypted_user = models.BinaryField(null=True, blank=True)
    encrypted_password = models.BinaryField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PixivCredential(updated_at={self.updated_at})"


class PoipikuCredential(models.Model):
    """Single-row store for Poipiku login cookies (see item.poipiku_creds).
    Mirrors TwitterCredential/PixivCredential: Fernet-encrypted, decryption
    key lives only in POIPIKU_CREDS_ENC_KEY outside the DB. Never exposed
    via any API response — see item.poipiku_creds_views (write-only).
    """
    encrypted_lk = models.BinaryField(null=True, blank=True)
    encrypted_jsessionid = models.BinaryField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PoipikuCredential(updated_at={self.updated_at})"


class DriveCredential(models.Model):
    """Single-row store for the Google Drive OAuth client + refresh token
    (see item.drive_creds) used by item.drive_backup. Same encrypted
    pattern as TwitterCredential/PixivCredential — decryption key lives
    only in DRIVE_CREDS_ENC_KEY outside the DB.

    Exists specifically so the exe build (no .env file a user could edit)
    can configure this entirely from the settings panel: client_id/
    client_secret are entered once, then item.drive_creds_views's
    authenticate endpoint runs the same OAuth flow
    scripts/google_drive_auth.py already does out-of-band, storing the
    resulting refresh_token here instead of printing it for a human to
    paste into .env.
    """
    encrypted_client_id = models.BinaryField(null=True, blank=True)
    encrypted_client_secret = models.BinaryField(null=True, blank=True)
    encrypted_refresh_token = models.BinaryField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"DriveCredential(updated_at={self.updated_at})"


class PixivPollState(models.Model):
    """Singleton health-state row for the Pixiv bookmark poller, mirroring
    TwitterPollState (see item.management.commands.poll_pixiv_bookmarks).
    Pixiv's bookmarks endpoint is simpler than Twitter's: no separate
    Likes concept, and it's directly offset-paginated (no opaque cursor),
    so there's just one resume position to track, and no separate
    Bookmarks/Likes cursor pair or screen_name resolution step needed.
    """
    resume_offset = models.IntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)

    def __str__(self):
        return f"PixivPollState(last_success_at={self.last_success_at})"


class PoipikuPollState(models.Model):
    """Singleton health-state row for the Poipiku bookmark ("お気に入り")
    poller, mirroring PixivPollState. `resume_url` (not an offset) since
    Poipiku's own bookmark-list pagination links already embed whatever
    the resume target should be (see item.poipiku_bookmarks_fetch's own
    docstring for why the next-page URL is read off the page itself
    rather than constructed here) -- storing the exact URL to continue
    from is simpler and more robust than reconstructing one from a bare
    page number.
    """
    resume_url = models.CharField(max_length=255, blank=True, default='')
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)

    def __str__(self):
        return f"PoipikuPollState(last_success_at={self.last_success_at})"


class SocialFetchQueueItem(models.Model):
    """FIFOキュー行1件 = ポーリングで見つかった、まだ取り込んでいない
    ブックマーク/いいね1件 (see item.management.commands.poll_twitter_updates
    and .poll_pixiv_bookmarks).

    `(platform, external_id)`がunique — 同じ投稿がブックマークと「いいね」の
    両方で見つかっても行は1つだけ持つ(kindは最初に見つかった方を保持)。
    platform単位なのは、Twitterのtweet IDとPixivのillust IDは無関係な採番な
    ので同じ数値が両方に存在しうるため(external_idだけのグローバルuniqueだと
    そこで衝突しうる)。
    `created_at`(=挿入順=id順)がそのままFIFOの処理順になる: discoveryは
    新規発見分を古い順に反転してから投入するので、キュー全体を
    id昇順で辿ればブックマーク/いいねした順に近い形で処理できる。
    """
    PLATFORM_CHOICES = [('twitter', 'twitter'), ('pixiv', 'pixiv'), ('poipiku', 'poipiku')]
    KIND_CHOICES = [('bookmark', 'bookmark'), ('like', 'like')]
    STATUS_CHOICES = [
        ('pending', 'pending'),
        ('done', 'done'),
        ('skipped', 'skipped'),
        ('failed', 'failed'),
    ]

    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES, default='twitter')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    external_id = models.BigIntegerField()
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
        unique_together = [('platform', 'external_id')]

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
