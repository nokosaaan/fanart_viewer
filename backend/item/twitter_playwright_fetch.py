import json
import logging
import re
import time
from urllib.parse import urlparse

from .twitter_creds import get_credentials
from .twitter_gql_fetch import (
    TwitterAuthError,
    TwitterGQLError,
    _extract_retweet_original,
    _extract_media_urls,
    _extract_full_text,
    _get_screen_name,
    _get_author_id,
    _get_rest_id,
    _unwrap_tweet,
    _resolve_screen_name_by_user_id,
    _extract_tweets_and_cursor_from_instructions,
)

logger = logging.getLogger(__name__)


def _extract_retweets_from_page_dom(page, seen_ids: set) -> list[dict]:
    """画面上の <article data-testid='tweet'> を走査し、
    リポスト（You reposted / ○○ reposted）かつ画像を含むツイートを抽出する。
    GraphQL レスポンスの取りこぼしに対する完全なフォールバックとして機能する。
    """
    results = []
    try:
        articles = page.locator("article[data-testid='tweet']").all()
        for art in articles:
            try:
                # 1. リポスト判定: socialContext (You reposted, ○○ reposted, リポストしました)
                has_rt = False
                social_context = art.locator("[data-testid='socialContext']")
                if social_context.count() > 0:
                    txt = social_context.first.inner_text().lower()
                    if "repost" in txt or "リポスト" in txt:
                        has_rt = True

                if not has_rt:
                    # 先頭テキストに reposted や リポスト が含まれているか確認
                    top_text = art.inner_text()[:120].lower()
                    if "repost" in top_text or "リポスト" in top_text:
                        has_rt = True

                if not has_rt:
                    continue

                # 2. ツイートのURLとIDの取得: status リンク
                status_links = art.locator("a[href*='/status/']").all()
                tweet_id = None
                author = None

                for link in status_links:
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/([A-Za-z0-9_]+)/status/(\d+)", href)
                    if m:
                        author = m.group(1)
                        tweet_id = m.group(2)
                        break

                if not tweet_id or tweet_id in seen_ids:
                    continue

                # 3. 画像の取得: tweetPhoto img
                photos = art.locator("div[data-testid='tweetPhoto'] img").all()
                media_urls = []
                for p_img in photos:
                    src = p_img.get_attribute("src") or ""
                    if "pbs.twimg.com/media/" in src:
                        base = src.split("?")[0]
                        media_urls.append(f"{base}?name=orig")

                if not media_urls:
                    continue

                # 4. 本文テキスト (tweetText)
                tweet_text_el = art.locator("[data-testid='tweetText']")
                desc = tweet_text_el.first.inner_text() if tweet_text_el.count() > 0 else ""

                results.append({
                    "tweet_id": str(tweet_id),
                    "screen_name": author,
                    "media_urls": media_urls,
                    "description": desc,
                })
            except Exception:
                continue
    except Exception:
        pass
    return results


def fetch_account_retweets_playwright(
    screen_name: str,
    max_items: int = 30,
    known_ids: set | None = None,
    max_scrolls: int = 15,
    timeout_ms: int = 30000,
) -> dict:
    """Playwright (Chromium) を使用して指定アカウントの /all ページ
    (https://x.com/<screen_name>/all) をブラウザで開き、
    通信(GraphQLレスポンス)およびDOMから画像付きネイティブRTを収集する。

    XのWeb UI上で「メイン画面では出たり消えたりするが /all タブなら表示される」
    ような不安定なアカウントやタイムラインでも、ブラウザが実際にレンダリングした
    データを直接インターセプトするため最も確実に取得できる。

    戻り値 (fetch_account_retweets と同一形状):
    {
        'screen_name': str, 'user_id': str, 'pages_fetched': int,
        'already_archived': int,
        'retweets': [{'tweet_id': str, 'screen_name': str|None,
                      'media_urls': [str, ...], 'description': str}, ...],
    }
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwrightがインストールされていません (`pip install playwright` を実行してください)") from e

    from .playwright_setup import ensure_chromium_installed
    ensure_chromium_installed()

    auth_token, ct0 = get_credentials()
    if not auth_token or not ct0:
        raise TwitterAuthError("Twitter認証情報が設定されていません (設定画面から auth_token / ct0 を登録してください)")

    screen_name = screen_name.strip().lstrip("@")
    target_url = f"https://x.com/{screen_name}/all"

    intercepted_tweets: list[dict] = []
    seen_ids: set[str] = set()
    retweets: list[dict] = []
    already_archived_count = 0
    resolved_authors: dict[str, str | None] = {}

    def _handle_response(resp):
        try:
            url = resp.url
            if "/graphql/" not in url:
                return
            content_type = resp.headers.get("content-type") or ""
            if "application/json" not in content_type:
                return

            try:
                data = resp.json()
            except Exception:
                return

            # Check if this GraphQL response contains user timeline instructions
            # (UserTweets, UserTweetsAndReplies, etc.)
            user_res = (data.get("data") or {}).get("user", {}).get("result") or {}
            timeline_data = user_res.get("timeline") or user_res.get("timeline_v2") or {}
            instructions = (timeline_data.get("timeline") or {}).get("instructions") or []
            if not instructions:
                # Also check top-level instructions (e.g. some timeline endpoints)
                instructions = (data.get("data") or {}).get("timeline", {}).get("instructions") or []

            if instructions:
                tweets, _ = _extract_tweets_and_cursor_from_instructions(instructions)
                for tw in tweets:
                    intercepted_tweets.append(tw)
        except Exception:
            pass

    with sync_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        browser = p.chromium.launch(headless=True, args=launch_args)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        # Inject session cookies for x.com and twitter.com
        cookies = []
        for domain in (".x.com", ".twitter.com"):
            cookies.append({"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"})
            cookies.append({"name": "ct0", "value": ct0, "domain": domain, "path": "/"})
        context.add_cookies(cookies)

        page = context.new_page()
        page.on("response", _handle_response)

        base_url = f"https://x.com/{screen_name}"
        target_all_url = f"https://x.com/{screen_name}/all"

        try:
            # ユーザーの実操作に合わせ、まず Posts (プロフィールトップ) を開いてから All タブへ移動する
            logger.info("twitter_playwright_fetch: navigating to %s (posts)", base_url)
            page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            # 「All」「すべて」「全件」または href に /all を含むタブを探してクリック
            clicked_tab = False
            tab_selectors = [
                f"a[role='tab'][href*='/{screen_name}/all']",
                f"a[href*='/{screen_name}/all']",
                "a[role='tab']:has-text('All')",
                "a[role='tab']:has-text('すべて')",
                "a[role='tab']:has-text('全件')",
                f"a[role='tab'][href*='with_replies']",
            ]
            for sel in tab_selectors:
                loc = page.locator(sel).first
                try:
                    if loc.is_visible(timeout=1000):
                        logger.info("twitter_playwright_fetch: clicking tab with selector %s", sel)
                        loc.click()
                        clicked_tab = True
                        page.wait_for_timeout(2500)
                        break
                except Exception:
                    continue

            if not clicked_tab:
                logger.info("twitter_playwright_fetch: tab not clickable in DOM, navigating directly to %s", target_all_url)
                page.goto(target_all_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2500)
        except Exception as e:
            browser.close()
            raise TwitterGQLError(f"Playwright navigation failed: {e}") from e

        scrolls = 0
        last_tweet_count = 0
        no_new_content_count = 0

        while scrolls < max_scrolls:
            # 1. Process whatever GraphQL tweets we've intercepted so far
            while intercepted_tweets:
                tweet = intercepted_tweets.pop(0)
                original = _extract_retweet_original(tweet)
                if not original:
                    continue  # native retweetでなければスキップ

                tweet_id = _get_rest_id(original)
                if not tweet_id or tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                tweet_id_int = None
                try:
                    tweet_id_int = int(tweet_id)
                except (ValueError, TypeError):
                    pass

                # 既に item に登録されているなら skip
                if known_ids and (tweet_id in known_ids or tweet_id_int in known_ids):
                    already_archived_count += 1
                    continue

                media_urls = _extract_media_urls(original)
                if not media_urls:
                    continue  # テキストのみのRTは保存対象外

                screen_name_for_rt = _get_screen_name(original)
                if not screen_name_for_rt:
                    author_id = _get_author_id(original)
                    if author_id:
                        if author_id not in resolved_authors:
                            resolved_authors[author_id] = _resolve_screen_name_by_user_id(
                                author_id, auth_token, ct0
                            )
                        screen_name_for_rt = resolved_authors[author_id]

                retweets.append({
                    "tweet_id": tweet_id,
                    "screen_name": screen_name_for_rt,
                    "media_urls": media_urls,
                    "description": _extract_full_text(original),
                })
                if len(retweets) >= max_items:
                    break

            if len(retweets) >= max_items:
                break

            # 2. DOM-level fallback extraction: 画面上の <article> から直接RTを補完取得
            dom_candidates = _extract_retweets_from_page_dom(page, seen_ids)
            for cand in dom_candidates:
                t_id = cand["tweet_id"]
                seen_ids.add(t_id)

                t_id_int = None
                try:
                    t_id_int = int(t_id)
                except (ValueError, TypeError):
                    pass

                if known_ids and (t_id in known_ids or t_id_int in known_ids):
                    already_archived_count += 1
                    continue

                retweets.append(cand)
                if len(retweets) >= max_items:
                    break

            if len(retweets) >= max_items:
                break

            # Scroll down to trigger pagination
            scrolls += 1
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(1500)

            # Check if new tweets were received
            total_seen = len(seen_ids)
            if total_seen == last_tweet_count:
                no_new_content_count += 1
                if no_new_content_count >= 3:
                    # End of timeline or stuck
                    break
            else:
                no_new_content_count = 0
            last_tweet_count = total_seen

        browser.close()

    logger.info(
        "twitter_playwright_fetch: for @%s finished: fetched=%d scrolls=%d already_archived=%d",
        screen_name, len(retweets), scrolls, already_archived_count,
    )

    return {
        "screen_name": screen_name,
        "user_id": "",
        "retweets": retweets,
        "pages_fetched": scrolls,
        "already_archived": already_archived_count,
    }

