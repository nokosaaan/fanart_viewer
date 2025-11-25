import os
import re
from urllib.parse import urljoin, urlparse
import base64
try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:
    HAVE_PLAYWRIGHT = False
try:
    # import the generic renderer to use as a fallback when helper finds nothing
    from .headless_fetch import fetch_rendered_media
    HAVE_RENDERER = True
except Exception:
    fetch_rendered_media = None
    HAVE_RENDERER = False


def fetch_images_with_playwright(target_url, headful=False, timeout_ms=12000):
    """Return list of (idx, bytes, content_type) fetched from target_url using Playwright login to Pixiv.
    Requires PIXIV_USER and PIXIV_PASS in env.
    """
    if not HAVE_PLAYWRIGHT:
        raise RuntimeError('playwright not available')

    pixiv_user = os.environ.get('PIXIV_USER') or os.environ.get('PIXIV_USERNAME')
    pixiv_pass = os.environ.get('PIXIV_PASS') or os.environ.get('PIXIV_PASSWORD')
    if not pixiv_user or not pixiv_pass:
        raise RuntimeError('PIXIV_USER/PIXIV_PASS not set in environment')

    results = []
    logged_in = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx = browser.new_context()
        page = ctx.new_page()

        # login
        login_url = 'https://accounts.pixiv.net/login'
        page.goto(login_url)
        try:
            if page.query_selector('input[name="pixiv_id"]'):
                page.fill('input[name="pixiv_id"]', pixiv_user)
            elif page.query_selector('input[id="LoginForm-username"]'):
                page.fill('input[id="LoginForm-username"]', pixiv_user)
            else:
                if page.query_selector('input[type="email"]'):
                    page.fill('input[type="email"]', pixiv_user)

            if page.query_selector('input[name="password"]'):
                page.fill('input[name="password"]', pixiv_pass)
            elif page.query_selector('input[id="LoginForm-password"]'):
                page.fill('input[id="LoginForm-password"]', pixiv_pass)

            if page.query_selector('button[type="submit"]'):
                page.click('button[type="submit"]')
            else:
                page.keyboard.press('Enter')
        except Exception:
            # proceed, maybe already logged in
            pass

        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass

        # after attempting login, inspect cookies to heuristically detect login success
        try:
            cookies = ctx.cookies()
            for c in cookies:
                if c.get('domain') and ('pixiv' in c.get('domain') or 'pximg' in c.get('domain')):
                    logged_in = True
                    break
        except Exception:
            logged_in = False

        # Attach a response listener to capture image responses made while
        # loading the target page. This captures requests that the logged-in
        # browser makes (with cookies) and avoids separate navigations.
        captured_responses = []
        def _on_response(r):
            try:
                u = r.url
            except Exception:
                return
            try:
                if 'pximg' in u or 'pixiv' in u:
                    headers = r.headers or {}
                    ct = headers.get('content-type','')
                    if ct and ct.split(';',1)[0].startswith('image'):
                        try:
                            b = r.body()
                        except Exception:
                            b = None
                        captured_responses.append({'url': u, 'body': b, 'content_type': ct, 'status': getattr(r, 'status', None)})
            except Exception:
                return

        # Ensure requests from the browser include a Referer; some pixiv
        # resources are guarded by referer checks. Set extra headers on the
        # context before navigating so subresource requests carry them.
        try:
            ctx.set_extra_http_headers({'Referer': 'https://www.pixiv.net/', 'User-Agent': 'fanart-viewer-bot/1.0'})
        except Exception:
            pass
        page.on('response', _on_response)
        page.goto(target_url)
        try:
            page.wait_for_load_state('networkidle', timeout=timeout_ms)
        except Exception:
            pass
        # Build a lookup map of captured responses by URL for quick access
        captured_map = {c['url']: (c['body'], c.get('content_type')) for c in captured_responses if c.get('body')}

        # If we didn't capture useful pixiv-hosted images yet, try to
        # interact with the page (click the first pixiv-hosted img) to
        # trigger lazy-loading / lightbox behaviors that request originals.
        if not captured_map and pixiv_imgs:
            try:
                imgs_handles = page.query_selector_all('img')
                clicked = False
                for ih in imgs_handles:
                    try:
                        src = ih.get_attribute('src')
                    except Exception:
                        src = None
                    if not src:
                        continue
                    if 'pximg' in src or 'pixiv' in src:
                        try:
                            # try clicking the parent anchor first
                            parent = ih.evaluate_handle('el => el.closest("a")')
                            if parent:
                                try:
                                    parent.as_element().click()
                                except Exception:
                                    try:
                                        ih.click()
                                    except Exception:
                                        pass
                            else:
                                try:
                                    ih.click()
                                except Exception:
                                    pass
                            clicked = True
                            page.wait_for_load_state('networkidle', timeout=5000)
                            break
                        except Exception:
                            continue
                # rebuild captured_map after interaction
                captured_map = {c['url']: (c['body'], c.get('content_type')) for c in captured_responses if c.get('body')}
            except Exception:
                pass

        imgs = []
        try:
            og = page.query_selector('meta[property="og:image"]')
            if og:
                v = og.get_attribute('content')
                if v:
                    imgs.append(v)
            for img in page.query_selector_all('img'):
                try:
                    src = img.get_attribute('src')
                except Exception:
                    src = None
                if src and src not in imgs:
                    imgs.append(src)
                try:
                    srcset = img.get_attribute('srcset') or img.get_attribute('data-srcset')
                except Exception:
                    srcset = None
                if srcset:
                    for part in (srcset or '').split(','):
                        try:
                            u = part.strip().split()[:1][0]
                            if u and u not in imgs:
                                imgs.append(u)
                        except Exception:
                            continue
                try:
                    ds = img.get_attribute('data-src') or img.get_attribute('data-original') or img.get_attribute('data-image-url')
                except Exception:
                    ds = None
                if ds and ds not in imgs:
                    imgs.append(ds)
        except Exception:
            pass

        if not imgs:
            # no immediate images found in DOM; we'll consider using the
            # generic renderer as a fallback later (after attempting pixiv fetches)
            imgs = []

        # helper to detect pixiv-hosted images
        def _is_pixiv_host(h):
            if not h:
                return False
            return 'pixiv' in h or 'pximg' in h or 'i.pximg.net' in h

        def make_pixiv_original_candidate(url):
            try:
                p = urlparse(url)
                net = p.netloc
                path = p.path
                if not _is_pixiv_host(net):
                    return url
                # remove /c/.../img-master prefix
                path = re.sub(r'^/c/\d+x\d+/img-master', '/img-master', path)
                path = path.replace('/img-master/', '/img-original/')
                path = re.sub(r'_master\d+(?=\.)', '', path)
                path = re.sub(r'(_p\d+)_master\d+', r'\1', path)
                # Ensure the common original path includes the extra 'img' segment
                # e.g. /img-original/img/2024/... which Pixiv uses for originals.
                if '/img-original/' in path and '/img-original/img/' not in path:
                    path = path.replace('/img-original/', '/img-original/img/', 1)
                return f"{p.scheme}://{p.netloc}{path}"
            except Exception:
                return url

        # Filter to pixiv artwork images and prefer p0/original
        pixiv_imgs = []
        for u in imgs:
            try:
                full = page.evaluate('(u)=>new URL(u, location.href).href', u)
            except Exception:
                full = u
            try:
                pu = urlparse(full)
                if _is_pixiv_host(pu.netloc):
                    pixiv_imgs.append(full)
            except Exception:
                continue

        # Prepare fetch candidates. For pixiv-hosted images, try multiple _pN variants
        # so callers can select the largest-bytes image. We'll attempt up to MAX_PAGES.
        candidates = []
        if pixiv_imgs:
            for u in pixiv_imgs:
                candidates.append(u)
        else:
            candidates = [imgs[0]]

        seen_urls = set()
        MAX_PAGES = 8
        # track whether we attempted a rendered fallback and any small candidates found
        fallback_used = False
        main_small_found = []
        pw_fallback_small_found = []
        # record every attempted fetch for debugging (url, size, content_type, phase)
        attempted_fetches = []
        # helper to fetch a candidate URL using the logged-in page context so
        # cookies and session headers are sent (avoids 403 from pixiv).
        # Build a cookie header string from context cookies so we can use it
        # in fallback requests (requests.get). This helps when ctx.request
        # doesn't include browser cookies automatically.
        cookie_header = None
        try:
            ck = ctx.cookies()
            parts = []
            for c in ck:
                if c.get('name') and c.get('value'):
                    parts.append(f"{c.get('name')}={c.get('value')}")
            if parts:
                cookie_header = '; '.join(parts)
        except Exception:
            cookie_header = None

        def _fetch_via_page(url, wait_for='networkidle', to_ms=10000):
            # First, attempt an in-page fetch via page.evaluate so the
            # browser's credentials, cookies and headers are used and we
            # can obtain the raw ArrayBuffer for the image. This often
            # succeeds where direct navigation or server-side requests
            # get 403s.
            try:
                try:
                    js = '''async (url, ref) => {
                        try {
                            const r = await fetch(url, { credentials: 'include', headers: { 'Referer': ref } });
                            const status = r.status;
                            const ct = r.headers.get('content-type');
                            if (!r.ok) {
                                return { ok: false, status, content_type: ct, b64: null };
                            }
                            const ab = await r.arrayBuffer();
                            const bytes = new Uint8Array(ab);
                            const chunk = 0x8000;
                            let binary = '';
                            for (let i = 0; i < bytes.length; i += chunk) {
                                binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunk)));
                            }
                            const b64 = btoa(binary);
                            return { ok: true, status, content_type: ct, b64 };
                        } catch (e) {
                            return { ok: false, status: 0, content_type: null, b64: null, err: String(e) };
                        }
                    }'''
                    pv = page.evaluate(js, url, 'https://www.pixiv.net/')
                except Exception:
                    pv = None
                if pv and isinstance(pv, dict) and pv.get('ok') and pv.get('b64'):
                    try:
                        b = base64.b64decode(pv.get('b64'))
                        return b, pv.get('content_type') or pv.get('content-type')
                    except Exception:
                        # fall through to other methods if decode fails
                        pass
            except Exception:
                pass

            # First try navigation via page (uses browser session)
            try:
                resp = page.goto(url, wait_until=wait_for, timeout=to_ms)
            except Exception:
                try:
                    resp = page.goto(url, timeout=to_ms)
                except Exception:
                    resp = None
            if resp:
                try:
                    status = resp.status
                except Exception:
                    status = None
                if status == 200:
                    try:
                        headers = resp.headers or {}
                        ct = headers.get('content-type', '')
                    except Exception:
                        ct = ''
                    if ct and ct.split(';', 1)[0].startswith('image'):
                        try:
                            body = resp.body()
                        except Exception:
                            body = None
                        return body, ct
            # If page navigation didn't yield a result, fall back to requests
            # using the cookies we captured from the browser context.
            try:
                import requests as _requests
                headers = {'Referer': 'https://www.pixiv.net/', 'User-Agent': 'fanart-viewer-bot/1.0'}
                if cookie_header:
                    headers['Cookie'] = cookie_header
                r = _requests.get(url, timeout=10, headers=headers, allow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get('content-type','')
                    if ct and ct.split(';',1)[0].startswith('image'):
                        return r.content, ct
            except Exception:
                pass
            return None, None

        for u in candidates:
            try:
                pu = urlparse(u)
                path = pu.path
            except Exception:
                pu = None
                path = ''

            if re.search(r'_p\d+', path):
                # build template to iterate p0..pN
                base_template = re.sub(r'(_p)\d+', r'\1{}', u)
                for i in range(0, MAX_PAGES):
                    try:
                        candidate_i = base_template.format(i)
                    except Exception:
                        continue
                    candidate_i = make_pixiv_original_candidate(candidate_i)
                    if candidate_i in seen_urls:
                        continue
                    seen_urls.add(candidate_i)
                    # Prefer a response captured during initial page load
                    if candidate_i in captured_map:
                        body, content_type = captured_map.get(candidate_i, (None, None))
                    else:
                        try:
                            body, content_type = _fetch_via_page(candidate_i)
                        except Exception:
                            body, content_type = None, None
                    attempted_fetches.append({'url': candidate_i, 'size': len(body) if body else 0, 'content_type': content_type, 'phase': 'main'})
                    # prefer substantial images; skip tiny assets
                    try:
                        if body and len(body) >= 10240:
                            results.append((i, body, content_type, candidate_i))
                        else:
                            main_small_found.append((candidate_i, len(body) if body else 0, content_type))
                    except Exception:
                        main_small_found.append((candidate_i, len(body) if body else 0, content_type))
            else:
                candidate_single = make_pixiv_original_candidate(u)
                if candidate_single in seen_urls:
                    continue
                seen_urls.add(candidate_single)
                if candidate_single in captured_map:
                    body, content_type = captured_map.get(candidate_single, (None, None))
                else:
                    try:
                        body, content_type = _fetch_via_page(candidate_single)
                    except Exception:
                        body, content_type = None, None
                attempted_fetches.append({'url': candidate_single, 'size': len(body) if body else 0, 'content_type': content_type, 'phase': 'main'})
                try:
                    if body and len(body) >= 10240:
                        results.append((0, body, content_type, candidate_single))
                    else:
                        main_small_found.append((candidate_single, len(body) if body else 0, content_type))
                except Exception:
                    main_small_found.append((candidate_single, len(body) if body else 0, content_type))

        # If helper didn't fetch any useful images from pixiv-hosted paths,
        # attempt a fallback: use the generic rendered-media extractor to
        # discover candidate URLs, then fetch them via Playwright's request
        # context. This helps in cases where the page requires rendering to
        # produce img-master URLs.
        fallback_used = False
        fallback_found = []
        try:
            if not results and HAVE_RENDERER and fetch_rendered_media:
                try:
                    pw_urls = fetch_rendered_media(target_url, browser_name='chromium', headless=not headful)
                except Exception:
                    pw_urls = []
                for h in pw_urls or []:
                    try:
                        if not h:
                            continue
                        # First try the rendered URL as-is (master1200 etc.)
                        if h in seen_urls:
                            continue
                        seen_urls.add(h)
                        if h in captured_map:
                            body, ct = captured_map.get(h, (None, None))
                        else:
                            try:
                                body, ct = _fetch_via_page(h)
                            except Exception:
                                body, ct = None, None
                        attempted_fetches.append({'url': h, 'size': len(body) if body else 0, 'content_type': ct, 'phase': 'pw_fallback'})
                        ok = False
                        if ct and ct.startswith('image') and body and len(body) >= 10240:
                            results.append((0, body, ct, h))
                            fallback_used = True
                            ok = True
                        else:
                            pw_fallback_small_found.append((h, len(body) if body else 0, ct))
                        if ok:
                            continue
                        # If raw rendered URL failed or was small, try the img-original candidate
                        candidate = make_pixiv_original_candidate(h)
                        if candidate in seen_urls:
                            continue
                        seen_urls.add(candidate)
                        if candidate in captured_map:
                            body2, content_type = captured_map.get(candidate, (None, None))
                        else:
                            try:
                                body2, content_type = _fetch_via_page(candidate)
                            except Exception:
                                body2, content_type = None, None
                        attempted_fetches.append({'url': candidate, 'size': len(body2) if body2 else 0, 'content_type': content_type, 'phase': 'pw_fallback'})
                        if body2 is None or len(body2) < 10240:
                            pw_fallback_small_found.append((candidate, len(body2) if body2 else 0, content_type))
                            continue
                        results.append((0, body2, content_type, candidate))
                        fallback_used = True
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            browser.close()
        except Exception:
            pass

    # Prepare richer debug info for callers
    debug = {
        'found_count': len(imgs),
        'pixiv_hosted_count': len(pixiv_imgs),
        'returned_count': len(results),
        'logged_in': logged_in,
        'fallback_used': fallback_used,
        'main_small_found_count': len(main_small_found),
        'pw_fallback_small_found_count': len(pw_fallback_small_found),
        'attempted_fetches': attempted_fetches,
    }
    return {'logged_in': logged_in, 'images': results, 'debug': debug}
