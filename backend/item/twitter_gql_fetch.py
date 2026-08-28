"""
Twitter/X sensitive image fetcher using the internal GraphQL API.

Twitter's web client (x.com) uses a private GraphQL endpoint (TweetDetail)
that returns full media data including for sensitive tweets — provided the
request carries a valid logged-in session.

This approach is:
  - Browser-free (plain HTTP via requests)
  - Not subject to headless-browser detection
  - Reliable for sensitive content when auth is valid

Required env vars:
  TWITTER_AUTH_TOKEN  auth_token cookie from a logged-in x.com session
  TWITTER_CT0         ct0 (CSRF) cookie from the same session

Both cookies come from the browser's cookie storage for x.com / twitter.com
after logging in. They typically stay valid for several months.
To refresh: open DevTools → Application → Cookies on x.com, copy the values.

The bearer token below is Twitter's own web-app bearer, publicly known and
embedded in Twitter's JS bundle. It does not expire.
"""

import json
import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

# Twitter web-app bearer token — embedded in Twitter's JS, publicly known.
_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL query ID for TweetDetail.
# This changes when Twitter redeploys; update from:
#   https://github.com/fa0311/twitter-openapi or gallery-dl source
_TWEET_DETAIL_QUERY_ID = "nBS-WpgA6ZG0CyNHD517JQ"

_TWEET_DETAIL_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

# Query IDs for UserByScreenName (username -> user id) and UserTweets (a
# user's reverse-chronological timeline, used here to scan for retweets —
# see fetch_account_retweets). Sourced from gallery-dl's twitter extractor
# (github.com/mikf/gallery-dl, gallery_dl/extractor/twitter.py) and verified
# live against the real endpoint (dummy-auth request reaches the app layer
# and returns a normal "Could not authenticate you" error rather than a 404,
# confirming the query ID itself is current).
_USER_BY_SCREEN_NAME_QUERY_ID = "ck5KkZ8t5cOmoLssopN99Q"
_USER_TWEETS_QUERY_ID = "E8Wq-_jFSaU7hxVcuOPR9g"
# UserByRestId — resolves a numeric user id to a screen_name. Used as a
# fallback when a retweet's original tweet result is missing its inline
# `core.user_results` hydration (observed on real, live, non-deleted tweets —
# not just the tombstoned/suspended case _get_screen_name's docstring
# originally assumed); `legacy.user_id_str` is present on the tweet either
# way, so it's always available to resolve from. Verified live (dummy-auth
# request reaches the app layer, same as the other query ids above).
_USER_BY_REST_ID_QUERY_ID = "8r5oa_2vD0WkhIAOkY4TTA"

_USER_BY_SCREEN_NAME_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "payments_enabled": False,
    "rweb_xchat_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
}

# Same as _USER_BY_SCREEN_NAME_FEATURES minus the two verification-info flags
# gallery-dl only adds for that specific query — used by UserByRestId.
_USER_BY_REST_ID_FEATURES = {
    k: v for k, v in _USER_BY_SCREEN_NAME_FEATURES.items()
    if not k.startswith("subscriptions_verification_info")
}

_USER_TWEETS_FEATURES = {
    "rweb_video_screen_enabled": False,
    "payments_enabled": False,
    "rweb_xchat_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}

# Default/ceiling for fetch_account_retweets' max_items. The default sits
# comfortably under the ~40-request threshold that was observed in practice
# to trigger a several-minute rate-limit lockout on other endpoints (search,
# etc.) on this account. Since each UserTweets page returns ~20-40 timeline
# entries already carrying full media/text (no per-tweet TweetDetail call
# needed — see fetch_account_retweets), collecting this many retweets costs
# only a handful of GraphQL requests, not one per item.
_ACCOUNT_RETWEETS_DEFAULT_MAX = 30
_ACCOUNT_RETWEETS_HARD_CAP = 100
# Safety valve against looping forever on an account whose timeline has very
# few retweets relative to its total tweet count.
_ACCOUNT_RETWEETS_MAX_PAGES = 20


def _get_creds() -> tuple[str, str]:
    """(auth_token, ct0), preferring the encrypted DB-stored value set via
    the admin UI and falling back to the TWITTER_AUTH_TOKEN/TWITTER_CT0 env
    vars — see item.twitter_creds. Imported lazily so this module stays
    importable (and its non-Django parts testable) without the Django app
    registry being ready.
    """
    from .twitter_creds import get_credentials

    return get_credentials()


class TwitterAuthError(RuntimeError):
    """auth_token / ct0 が期限切れまたは未設定。"""


class TwitterGQLError(RuntimeError):
    """GraphQL 呼び出し失敗 (HTTP エラー等)。"""


def _build_headers(auth_token: str, ct0: str) -> dict:
    return {
        "Authorization": f"Bearer {_BEARER}",
        "x-csrf-token": ct0,
        "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "ja",
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.9",
        "Origin": "https://x.com",
        "Referer": "https://x.com/",
        "Content-Type": "application/json",
    }


def _extract_media_urls(tweet_obj: dict) -> list[str]:
    """tweet_results.result (or .result.tweet) から画像/動画URLを返す。"""
    # unwrap nested tweet wrapper if present
    if "tweet" in tweet_obj:
        tweet_obj = tweet_obj["tweet"]

    urls = []
    legacy = tweet_obj.get("legacy") or {}
    ext = legacy.get("extended_entities") or legacy.get("entities") or {}
    for m in ext.get("media") or []:
        mtype = m.get("type", "")
        if mtype == "photo":
            base = m.get("media_url_https") or m.get("media_url")
            if base:
                # ?name=orig is the original-resolution variant
                urls.append(base.rstrip("?") + "?name=orig")
        elif mtype in ("video", "animated_gif"):
            variants = (m.get("video_info") or {}).get("variants") or []
            mp4 = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4:
                best = max(mp4, key=lambda v: v.get("bitrate") or 0)
                if best.get("url"):
                    urls.append(best["url"])
    return urls


def _extract_full_text(tweet_obj: dict) -> str:
    """tweet_results.result から本文テキストを返す。

    140字超のツイートは legacy.full_text が切り詰められ、本文全体は
    note_tweet.note_tweet_results.result.text 側に入る（longform notetweets)。
    ハッシュタグ判定に使うので、あれば note_tweet を優先する。
    """
    if "tweet" in tweet_obj:
        tweet_obj = tweet_obj["tweet"]

    note_text = (
        ((tweet_obj.get("note_tweet") or {}).get("note_tweet_results") or {})
        .get("result", {})
        .get("text")
    )
    if note_text:
        return note_text

    legacy = tweet_obj.get("legacy") or {}
    return legacy.get("full_text") or ""


def _unwrap_tweet(tweet_obj: dict) -> dict:
    """TweetWithVisibilityResults 等のラッパーを剥がして tweet dict を返す。"""
    return tweet_obj.get("tweet", tweet_obj)


def _get_rest_id(tweet_obj: dict) -> str | None:
    """tweet result から tweet ID を返す。"""
    t = _unwrap_tweet(tweet_obj)
    return t.get("rest_id") or t.get("legacy", {}).get("id_str")


def _get_author_id(tweet_obj: dict) -> str | None:
    """tweet result から投稿者の user_id_str を返す。"""
    t = _unwrap_tweet(tweet_obj)
    return t.get("legacy", {}).get("user_id_str")


def _collect_all_tweet_results(obj, results: list):
    """GQL レスポンス全体を再帰走査して tweet result オブジェクトを収集する。"""
    if isinstance(obj, dict):
        if "tweet_results" in obj:
            result = (obj["tweet_results"] or {}).get("result")
            if result:
                results.append(result)
        for v in obj.values():
            _collect_all_tweet_results(v, results)
    elif isinstance(obj, list):
        for item in obj:
            _collect_all_tweet_results(item, results)


def _walk_for_media(obj, collected: list):
    """GQL レスポンス全体を再帰的に走査して tweet_results を見つける。"""
    if isinstance(obj, dict):
        if "tweet_results" in obj:
            result = (obj["tweet_results"] or {}).get("result") or {}
            collected.extend(_extract_media_urls(result))
        for v in obj.values():
            _walk_for_media(v, collected)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_media(item, collected)


def fetch_tweet_media_urls(tweet_url: str, auth_token: str, ct0: str) -> tuple[list[str], str]:
    """
    ツイートURLから (画像/動画のURLリスト, 本文テキスト) を返す。

    本文テキストは主ツイート（スレッドの起点、tweet_id に一致するもの）のみ。
    ハッシュタグ抽出等に使うので、見つからなければ空文字列。

    センシティブコンテンツも含む。
    pbs.twimg.com の URL は CDN なので認証なしにダウンロード可能。

    Raises:
        TwitterAuthError: auth_token / ct0 が無効または期限切れ
        TwitterGQLError:  HTTP エラー等
    """
    m = re.search(r"/status/(\d+)", tweet_url)
    if not m:
        raise ValueError(f"Not a tweet URL: {tweet_url}")
    tweet_id = m.group(1)

    variables = json.dumps(
        {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        },
        separators=(",", ":"),
    )
    features = json.dumps(_TWEET_DETAIL_FEATURES, separators=(",", ":"))

    endpoint = f"https://twitter.com/i/api/graphql/{_TWEET_DETAIL_QUERY_ID}/TweetDetail"
    headers = _build_headers(auth_token, ct0)

    try:
        resp = requests.get(
            endpoint,
            params={"variables": variables, "features": features},
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as e:
        raise TwitterGQLError(f"Request failed: {e}") from e

    if resp.status_code in (401, 403):
        raise TwitterAuthError(
            "auth_token または ct0 が期限切れです。"
            "ブラウザの x.com Cookie から再取得してください。"
        )
    if not resp.ok:
        raise TwitterGQLError(f"Twitter GraphQL returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise TwitterGQLError(f"Invalid JSON response: {e}") from e

    # Check for auth errors embedded in the JSON body
    errors = data.get("errors") or []
    for err in errors:
        code = err.get("code")
        if code in (32, 64, 135, 326):  # Bad/suspended/expired auth codes
            raise TwitterAuthError(f"Twitter auth error code {code}: {err.get('message')}")

    # Collect all tweet result objects from the thread/conversation
    all_results: list[dict] = []
    _collect_all_tweet_results(data, all_results)

    # Find the main tweet to identify the author + capture its body text
    main_author_id: str | None = None
    description = ""
    for r in all_results:
        if _get_rest_id(r) == tweet_id:
            main_author_id = _get_author_id(r)
            description = _extract_full_text(r)
            break

    # Collect media from main tweet + same-author thread continuations only
    media_urls: list[str] = []
    for r in all_results:
        if main_author_id is None or _get_author_id(r) == main_author_id:
            media_urls.extend(_extract_media_urls(r))

    if not media_urls and not all_results:
        # Fallback: walk the raw response (old behaviour) in case structure changed
        _walk_for_media(data, media_urls)

    # deduplicate while preserving order
    return list(dict.fromkeys(media_urls)), description


def fetch_twitter_media(tweet_url: str) -> tuple[list[tuple[bytes, str]], str]:
    """
    ツイートURLから ([(画像バイト, MIMEタイプ), ...], 本文テキスト) を返す。

    views.py から呼び出すメインエントリポイント。
    TWITTER_AUTH_TOKEN / TWITTER_CT0 環境変数を使う。

    Raises:
        TwitterAuthError: 認証エラー
        RuntimeError: 環境変数未設定
    """
    auth_token, ct0 = _get_creds()

    if not auth_token:
        raise RuntimeError("TWITTER_AUTH_TOKEN が設定されていません")
    if not ct0:
        raise RuntimeError("TWITTER_CT0 が設定されていません")

    media_urls, description = fetch_tweet_media_urls(tweet_url, auth_token, ct0)
    if not media_urls:
        logger.warning("twitter_gql_fetch: No media URLs found for %s", tweet_url)
        return [], description

    dl_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        # pbs.twimg.com CDN doesn't require auth, but sending Referer helps
        "Referer": "https://x.com/",
    }

    results = []
    for url in media_urls:
        try:
            r = requests.get(url, headers=dl_headers, timeout=30)
            if not r.ok:
                logger.warning("twitter_gql_fetch: image download %s → HTTP %s", url, r.status_code)
                continue
            mime = r.headers.get("content-type", "image/jpeg").split(";", 1)[0].lower()
            if mime.startswith("image") or mime.startswith("video"):
                results.append((r.content, mime))
        except requests.RequestException as e:
            logger.warning("twitter_gql_fetch: download failed %s: %s", url, e)

    return results, description


def _get_with_ratelimit_backoff(url: str, params: dict, headers: dict, max_retries: int = 5):
    """GET を投げ、Twitterのレート制限ヘッダーを尊重してリトライする。

    ステータス429、または残り回数(`x-rate-limit-remaining`)がほぼ0の応答は
    「そのまま叩き続けるとアカウントレベルでより長くロックされるサイン」
    として扱い、`x-rate-limit-reset` (Unixタイム、無ければ60秒) まで待って
    からリトライする。gallery-dl の同種の実装を参考にした挙動。
    """
    resp = None
    for _ in range(max_retries):
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        remaining = resp.headers.get("x-rate-limit-remaining")
        rate_limited = resp.status_code == 429
        try:
            if remaining is not None and int(remaining) <= 1:
                rate_limited = True
        except ValueError:
            pass
        if not rate_limited:
            return resp
        reset_at = resp.headers.get("x-rate-limit-reset")
        try:
            wait_s = max(1.0, float(reset_at) - time.time()) if reset_at else 60.0
        except (TypeError, ValueError):
            wait_s = 60.0
        wait_s = min(wait_s, 300.0)
        logger.warning("twitter_gql_fetch: rate limited, waiting %.0fs before retry", wait_s)
        time.sleep(wait_s)
    return resp


def _resolve_user_id(screen_name: str, auth_token: str, ct0: str) -> str:
    """screen_name(先頭@なし)からユーザーの rest_id を返す。"""
    variables = json.dumps(
        {"screen_name": screen_name, "withGrokTranslatedBio": False},
        separators=(",", ":"),
    )
    features = json.dumps(_USER_BY_SCREEN_NAME_FEATURES, separators=(",", ":"))
    field_toggles = json.dumps({"withAuxiliaryUserLabels": True}, separators=(",", ":"))

    endpoint = f"https://twitter.com/i/api/graphql/{_USER_BY_SCREEN_NAME_QUERY_ID}/UserByScreenName"
    headers = _build_headers(auth_token, ct0)

    try:
        resp = _get_with_ratelimit_backoff(
            endpoint,
            {"variables": variables, "features": features, "fieldToggles": field_toggles},
            headers,
        )
    except requests.RequestException as e:
        raise TwitterGQLError(f"Request failed: {e}") from e

    if resp.status_code in (401, 403):
        raise TwitterAuthError(
            "auth_token または ct0 が期限切れです。"
            "ブラウザの x.com Cookie から再取得してください。"
        )
    if not resp.ok:
        raise TwitterGQLError(f"Twitter GraphQL returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise TwitterGQLError(f"Invalid JSON response: {e}") from e

    errors = data.get("errors") or []
    for err in errors:
        code = err.get("code")
        if code in (32, 64, 135, 326):
            raise TwitterAuthError(f"Twitter auth error code {code}: {err.get('message')}")

    user = ((data.get("data") or {}).get("user") or {}).get("result") or {}
    rest_id = user.get("rest_id")
    if not rest_id:
        raise ValueError(f"User not found: {screen_name}")
    return rest_id


def _resolve_screen_name_by_user_id(user_id: str, auth_token: str, ct0: str) -> str | None:
    """Numeric user id -> screen_name, or None on any failure.

    Best-effort fallback only (see fetch_account_retweets) — never raises,
    since a single lookup failing shouldn't abort the whole scan; the
    caller just keeps the artist blank for that one item when this misses.
    """
    variables = json.dumps({"userId": user_id}, separators=(",", ":"))
    features = json.dumps(_USER_BY_REST_ID_FEATURES, separators=(",", ":"))
    endpoint = f"https://twitter.com/i/api/graphql/{_USER_BY_REST_ID_QUERY_ID}/UserByRestId"
    headers = _build_headers(auth_token, ct0)

    try:
        resp = _get_with_ratelimit_backoff(endpoint, {"variables": variables, "features": features}, headers)
        if not resp.ok:
            logger.warning(
                "twitter_gql_fetch: UserByRestId HTTP %s for user_id=%s: %s",
                resp.status_code, user_id, resp.text[:300],
            )
            return None
        data = resp.json()
        errors = data.get("errors") or []
        if errors:
            logger.warning("twitter_gql_fetch: UserByRestId errors for user_id=%s: %s", user_id, errors)
        user = ((data.get("data") or {}).get("user") or {}).get("result") or {}
        screen_name = (user.get("legacy") or {}).get("screen_name")
        if not screen_name:
            logger.warning(
                "twitter_gql_fetch: UserByRestId returned no usable screen_name for user_id=%s "
                "(typename=%s, keys=%s)",
                user_id, user.get("__typename"), sorted(user.keys()),
            )
        return screen_name or None
    except (requests.RequestException, ValueError) as e:
        logger.warning("twitter_gql_fetch: UserByRestId lookup failed for user_id=%s: %s", user_id, e)
        return None


def _extract_retweet_original(tweet_obj: dict):
    """tweet_results.result がネイティブRTなら、オリジナルツイートの
    tweet dict(rest_id/legacy/coreを含む、アンラップ済み)を返す。RTで
    なければ None。

    引用RT(quote tweet)は legacy.quoted_status_result 側に入るため、ここ
    ではヒットしない — 拾いたいのはコメントなしの純粋なRTのみ。
    """
    t = _unwrap_tweet(tweet_obj)
    legacy = t.get("legacy") or {}
    rt = legacy.get("retweeted_status_result")
    if not rt:
        return None
    return _unwrap_tweet(rt.get("result") or {})


def _get_screen_name(tweet_obj: dict) -> str | None:
    """tweet result からツイート主の screen_name (@なし) を返す。"""
    t = _unwrap_tweet(tweet_obj)
    user = (((t.get("core") or {}).get("user_results") or {}).get("result") or {})
    return (user.get("legacy") or {}).get("screen_name")


def fetch_account_retweets(screen_name: str, max_items: int = None) -> dict:
    """指定アカウントのタイムラインを新しい順に走査し、ネイティブRTを
    最大 max_items 件、画像URL・本文つきで集めて返す。

    views.py から呼び出すエントリポイント。TWITTER_AUTH_TOKEN / TWITTER_CT0
    環境変数を使う。UserTweets は1回の呼び出しで最大40件程度のタイムライン
    項目を画像/本文つきで返すため、RT1件ごとに TweetDetail を叩く
    (=bookmark_fetch 経由の取得) より GraphQL 呼び出し回数がずっと少なく
    済む — 実運用で「一度に40件fetchするとレート制限で数分search等が
    使えなくなる」ことが確認されているため、この呼び出し回数の少なさが
    そのまま安全マージンになる。

    戻り値: {
        'screen_name': str, 'user_id': str, 'pages_fetched': int,
        'retweets': [{'tweet_id': str, 'screen_name': str|None,
                      'media_urls': [str, ...], 'description': str}, ...],
    }
    画像の無いRT(テキストのみの引用元など)はこのアプリでは保存しようが
    ないため、その場でスキップする。

    Raises:
        TwitterAuthError: 認証エラー
        TwitterGQLError: HTTPエラー等
        RuntimeError: 環境変数未設定
        ValueError: 指定アカウントが見つからない
    """
    auth_token, ct0 = _get_creds()
    if not auth_token:
        raise RuntimeError("TWITTER_AUTH_TOKEN が設定されていません")
    if not ct0:
        raise RuntimeError("TWITTER_CT0 が設定されていません")

    if max_items is None:
        max_items = _ACCOUNT_RETWEETS_DEFAULT_MAX
    max_items = max(1, min(int(max_items), _ACCOUNT_RETWEETS_HARD_CAP))

    screen_name = screen_name.lstrip("@")
    user_id = _resolve_user_id(screen_name, auth_token, ct0)
    headers = _build_headers(auth_token, ct0)
    endpoint = f"https://twitter.com/i/api/graphql/{_USER_TWEETS_QUERY_ID}/UserTweets"

    retweets: list[dict] = []
    seen_ids: set[str] = set()
    # user_id -> screen_name, populated by the _resolve_screen_name_by_user_id
    # fallback below. Keeps a prolific artist appearing in many RTs on this
    # timeline to a single extra lookup instead of one per retweet.
    resolved_authors: dict[str, str | None] = {}
    cursor: str | None = None
    pages = 0

    while len(retweets) < max_items and pages < _ACCOUNT_RETWEETS_MAX_PAGES:
        variables = {
            "userId": user_id,
            "count": 40,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(_USER_TWEETS_FEATURES, separators=(",", ":")),
            "fieldToggles": json.dumps({"withArticlePlainText": False}, separators=(",", ":")),
        }

        # Twitter occasionally returns some tweet results in a page without
        # their 'core' (author) data populated — a known API glitch gallery-dl
        # also works around by retrying the same page. Left unhandled, this
        # silently produced wrong-artist Items here (the RT's original author
        # couldn't be read, so a naive fallback used the scanned account's own
        # name instead) — this only shows up past the first tweet or two,
        # since the first page's leading entries are the ones most reliably
        # fully hydrated.
        entries = []
        for page_attempt in range(3):
            try:
                resp = _get_with_ratelimit_backoff(endpoint, params, headers)
            except requests.RequestException as e:
                raise TwitterGQLError(f"Request failed: {e}") from e

            if resp.status_code in (401, 403):
                raise TwitterAuthError(
                    "auth_token または ct0 が期限切れです。"
                    "ブラウザの x.com Cookie から再取得してください。"
                )
            if not resp.ok:
                raise TwitterGQLError(f"Twitter GraphQL returned HTTP {resp.status_code}: {resp.text[:200]}")

            try:
                data = resp.json()
            except ValueError as e:
                raise TwitterGQLError(f"Invalid JSON response: {e}") from e

            errors = data.get("errors") or []
            for err in errors:
                code = err.get("code")
                if code in (32, 64, 135, 326):
                    raise TwitterAuthError(f"Twitter auth error code {code}: {err.get('message')}")

            try:
                instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
            except (KeyError, TypeError):
                entries = []
                break

            entries = []
            for instr in instructions:
                if instr.get("type") == "TimelineAddEntries":
                    entries.extend(instr.get("entries") or [])

            incomplete = False
            for entry in entries:
                entry_id = entry.get("entryId") or ""
                if not entry_id.startswith("tweet-"):
                    continue
                content = entry.get("content") or {}
                item_content = content.get("itemContent") or {}
                tweet = (item_content.get("tweet_results") or {}).get("result")
                if not tweet:
                    continue
                if "core" not in _unwrap_tweet(tweet):
                    incomplete = True
                    break

            if not incomplete or page_attempt == 2:
                break
            time.sleep(1.5)

        pages += 1
        next_cursor = None
        for entry in entries:
            entry_id = entry.get("entryId") or ""
            if entry_id.startswith("cursor-bottom-"):
                next_cursor = (entry.get("content") or {}).get("value")
                continue
            if not entry_id.startswith("tweet-"):
                continue

            content = entry.get("content") or {}
            item_content = content.get("itemContent") or {}
            tweet = (item_content.get("tweet_results") or {}).get("result")
            if not tweet:
                continue

            original = _extract_retweet_original(tweet)
            if not original:
                continue  # native retweetでなければスキップ

            tweet_id = _get_rest_id(original)
            if not tweet_id or tweet_id in seen_ids:
                continue
            media_urls = _extract_media_urls(original)
            if not media_urls:
                continue  # テキストのみのRTは保存対象外

            seen_ids.add(tweet_id)

            screen_name_for_rt = _get_screen_name(original)
            if not screen_name_for_rt:
                # The inline core.user_results hydration is sometimes absent
                # even on perfectly live, public tweets (not just deleted or
                # suspended ones) — legacy.user_id_str is present regardless,
                # so resolve the screen_name with one extra lookup rather
                # than leaving the artist blank.
                author_id = _get_author_id(original)
                if author_id:
                    if author_id not in resolved_authors:
                        resolved_authors[author_id] = _resolve_screen_name_by_user_id(author_id, auth_token, ct0)
                    screen_name_for_rt = resolved_authors[author_id]
                if not screen_name_for_rt:
                    logger.warning(
                        "twitter_gql_fetch: could not resolve author for retweeted tweet %s "
                        "(author_id=%s) — leaving artist blank", tweet_id, author_id,
                    )

            retweets.append({
                "tweet_id": tweet_id,
                "screen_name": screen_name_for_rt,
                "media_urls": media_urls,
                "description": _extract_full_text(original),
            })
            if len(retweets) >= max_items:
                break

        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        if len(retweets) < max_items and pages < _ACCOUNT_RETWEETS_MAX_PAGES:
            time.sleep(1.5)  # ページ間の礼儀的なポーズ

    return {
        "screen_name": screen_name,
        "user_id": user_id,
        "retweets": retweets,
        "pages_fetched": pages,
    }


def verify_credentials() -> dict:
    """
    認証情報が有効かテストする。結果を dict で返す。
    管理画面や診断エンドポイントから呼び出す用途向け。

    twitter.com の内部 API を使う（api.twitter.com の v1.1 は OAuth 1.0a 必須）。
    """
    auth_token, ct0 = _get_creds()

    if not auth_token or not ct0:
        return {"ok": False, "reason": "TWITTER_AUTH_TOKEN または TWITTER_CT0 が未設定"}

    try:
        # twitter.com の内部 v1.1 エンドポイントは Bearer + Cookie で呼べる
        resp = requests.get(
            "https://twitter.com/i/api/1.1/account/verify_credentials.json",
            headers=_build_headers(auth_token, ct0),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"ok": True, "screen_name": data.get("screen_name"), "id": data.get("id_str")}
        return {"ok": False, "reason": f"HTTP {resp.status_code}", "body": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
