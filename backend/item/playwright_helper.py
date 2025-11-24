import os
import re
from urllib.parse import urljoin, urlparse
try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:
    HAVE_PLAYWRIGHT = False


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

        page.goto(target_url)
        try:
            page.wait_for_load_state('networkidle', timeout=timeout_ms)
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
                src = img.get_attribute('src')
                if src and src not in imgs:
                    imgs.append(src)
        except Exception:
            pass

        if not imgs:
            browser.close()
            return {'logged_in': logged_in, 'images': []}

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
                    try:
                        r = ctx.request.get(candidate_i, headers={'Referer': 'https://www.pixiv.net/'})
                    except Exception:
                        continue
                    if r.status != 200:
                        continue
                    content_type = r.headers.get('content-type','')
                    if not content_type.startswith('image'):
                        continue
                    body = r.body()
                    results.append((i, body, content_type, candidate_i))
            else:
                candidate_single = make_pixiv_original_candidate(u)
                if candidate_single in seen_urls:
                    continue
                seen_urls.add(candidate_single)
                try:
                    r = ctx.request.get(candidate_single, headers={'Referer': 'https://www.pixiv.net/'})
                except Exception:
                    continue
                if r.status != 200:
                    continue
                content_type = r.headers.get('content-type','')
                if not content_type.startswith('image'):
                    continue
                body = r.body()
                results.append((0, body, content_type, candidate_single))

        browser.close()

    return {'logged_in': logged_in, 'images': results}
