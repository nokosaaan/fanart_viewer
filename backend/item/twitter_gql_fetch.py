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
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
    ct0 = os.environ.get("TWITTER_CT0", "").strip()

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


def verify_credentials() -> dict:
    """
    認証情報が有効かテストする。結果を dict で返す。
    管理画面や診断エンドポイントから呼び出す用途向け。

    twitter.com の内部 API を使う（api.twitter.com の v1.1 は OAuth 1.0a 必須）。
    """
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
    ct0 = os.environ.get("TWITTER_CT0", "").strip()

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
