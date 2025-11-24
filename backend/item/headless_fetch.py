"""Headless renderer helper using Playwright.

This module provides a small CLI-friendly helper that opens a real
browser (Chromium/Firefox/WebKit via Playwright), waits for the page
to render, then extracts image URLs from `#react-root` (img src/srcset,
data-src, and background-image). It prints JSON to stdout when run as
a script.

Requirements:
  pip install playwright
  playwright install

Usage example:
  python3 backend/item/headless_fetch.py --url "https://twitter.com/.../status/ID"

The script returns a JSON array of discovered URLs.
"""

from typing import List
import json

def fetch_rendered_media(url: str, browser_name: str = 'chromium', headless: bool = True, timeout: int = 30000) -> List[str]:
    """Render the page and extract media URLs under #react-root.

    Returns a list of unique URLs (strings). Requires Playwright to be
    installed in the running environment.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError('Playwright not installed; run `pip install playwright`') from e

    import os
    with sync_playwright() as p:
      browser_ctor = getattr(p, browser_name, None)
      if browser_ctor is None:
        raise RuntimeError(f'Unsupported browser: {browser_name}')
      # Launch browser with some flags to reduce automation detection surface
      launch_args = [
        '--disable-blink-features=AutomationControlled',
        '--disable-dev-shm-usage',
      ]
      # If a system Chrome/Brave binary exists in common locations, prefer it
      chrome_paths = ['/usr/bin/google-chrome-stable', '/usr/bin/google-chrome', '/usr/bin/chrome', '/usr/bin/brave-browser', '/usr/bin/brave']
      chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)
      if chrome_exe and browser_name == 'chromium':
        try:
          browser = browser_ctor.launch(headless=headless, args=launch_args, executable_path=chrome_exe)
        except Exception:
          # fallback to bundled
          browser = browser_ctor.launch(headless=headless, args=launch_args)
      else:
        browser = browser_ctor.launch(headless=headless, args=launch_args)
      # Create a context that resembles a regular Chrome/Brave environment
      ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
      context = browser.new_context(
        user_agent=ua,
        locale='ja-JP',
        timezone_id='Asia/Tokyo',
        viewport={'width': 1280, 'height': 800},
      )
      # Try to mask the webdriver flag
      try:
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
      except Exception:
        pass
      page = context.new_page()

      try:
        page.goto(url, wait_until='networkidle', timeout=timeout)
      except Exception:
        # fallback: try waiting for selector; if that also fails, continue
        try:
          page.wait_for_selector('#react-root', timeout=timeout)
        except Exception:
          pass

      # perform gentle scrolls to trigger lazy-loading images
      try:
        for y in (200, 600, 1000, 1400):
          page.evaluate(f"window.scrollTo(0, {y})")
          page.wait_for_timeout(600)
      except Exception:
        pass

      # Evaluate inside the page to collect image URLs
      js = r"""
        () => {
          const root = document.getElementById('react-root');
          const urls = new Set();
          if (!root) {
            return [];
          }
          // collect <img> tags
          root.querySelectorAll('img').forEach(im => {
            try {
              const src = im.src;
              if (src && !src.startsWith('data:')) urls.add(src);
            } catch(e) {}
            const srcset = im.srcset || im.getAttribute('data-srcset');
            if (srcset) {
              srcset.split(',').forEach(part => {
                const url = part.trim().split(/\s+/)[0];
                if (url && !url.startsWith('data:')) urls.add(url);
              });
            }
            const ds = im.getAttribute('data-src') || im.getAttribute('data-image-url');
            if (ds && !ds.startsWith('data:')) urls.add(ds);
          });

          // collect background-image from computed styles
          root.querySelectorAll('*').forEach(el => {
            try {
              const s = window.getComputedStyle(el).getPropertyValue('background-image');
              if (s && s !== 'none') {
                const m = s.match(/url\((?:"|')?(.*?)(?:"|')?\)/);
                if (m && m[1] && !m[1].startsWith('data:')) urls.add(m[1]);
              }
            } catch(e) {}
          });

          // anchors linking to /photo/ may wrap images
          root.querySelectorAll('a[href*="/photo/"]').forEach(a => {
            const im = a.querySelector('img');
            if (im && im.src && !im.src.startsWith('data:')) urls.add(im.src);
            // also check background-image inside anchor
            a.querySelectorAll('*').forEach(el => {
              try {
                const s = window.getComputedStyle(el).getPropertyValue('background-image');
                if (s && s !== 'none') {
                  const m = s.match(/url\((?:"|')?(.*?)(?:"|')?\)/);
                  if (m && m[1] && !m[1].startsWith('data:')) urls.add(m[1]);
                }
              } catch(e) {}
            });
          });

          return Array.from(urls);
        }
        """

      try:
        urls = page.evaluate(js)
      except Exception:
        urls = []

      # If not much found, try broader selectors and clicking different elements
      if not urls or len(urls) < 2:
        extra_selectors = [
          'div[data-testid="tweetPhoto"] img',
          'figure img',
          'article img',
          'div[role="button"] img',
        ]
        for sel in extra_selectors:
          try:
            els = page.query_selector_all(sel)
            for el in els:
              try:
                src = el.get_attribute('src') or el.get_attribute('data-src')
                if src and not src.startswith('data:') and src not in urls:
                  urls.append(src)
              except Exception:
                continue
            # click parent to try opening viewer if present
            for el in els:
              try:
                el.click()
                page.wait_for_timeout(900)
                # extract pbs images after click
                pbs_found = page.evaluate(r"() => Array.from(document.querySelectorAll('img')).map(i=>i.src).filter(s=>s && s.includes('pbs.twimg.com'))")
                for u in pbs_found:
                  if u and u not in urls:
                    urls.append(u)
                try:
                  page.keyboard.press('Escape')
                  page.wait_for_timeout(200)
                except Exception:
                  pass
              except Exception:
                continue
          except Exception:
            continue

      # If we only found small assets (emoji/SVG) or want to be thorough,
      # try clicking gallery anchors (links to /photo/) to open the media
      # viewer/modal and collect full-size `pbs.twimg.com` images.
      try:
        anchors = page.query_selector_all('a[href*="/photo/"]')
        for i, a in enumerate(anchors):
          try:
            # click the thumbnail to open modal
            a.click()
            # give the modal a short time to appear and load
            page.wait_for_timeout(500)
            # wait for an image likely from pbs.twimg to appear in modal
            try:
              page.wait_for_selector('img[src*="pbs.twimg.com"]', timeout=3000)
            except Exception:
              # if not found, continue but still attempt to extract
              pass
            # extract pbs urls from the whole document (covers modal)
            modal_js = r"""
            () => {
              const urls = new Set();
              document.querySelectorAll('img').forEach(im => {
                try {
                  const s = im.src || '';
                  if (s.includes('pbs.twimg.com') && !s.startsWith('data:')) urls.add(s);
                } catch(e) {}
              });
              // also check computed background-images
              document.querySelectorAll('*').forEach(el => {
                try {
                  const s = window.getComputedStyle(el).getPropertyValue('background-image');
                  if (s && s !== 'none') {
                    const m = s.match(/url\((?:"|')?(.*?)(?:"|')?\)/);
                    if (m && m[1] && m[1].includes('pbs.twimg.com')) urls.add(m[1]);
                  }
                } catch(e) {}
              });
              return Array.from(urls);
            }
            """
            try:
              pbs = page.evaluate(modal_js)
            except Exception:
              pbs = []
            for u in (pbs or []):
              if u and u not in urls:
                urls.append(u)
            # close the modal if possible (Escape key)
            try:
              page.keyboard.press('Escape')
              page.wait_for_timeout(200)
            except Exception:
              pass
          except Exception:
            continue
      except Exception:
        # don't fail if this enhancement errors
        pass
      try:
        browser.close()
      except Exception:
        pass
      return list(dict.fromkeys(urls))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fetch rendered media URLs under #react-root using Playwright')
    parser.add_argument('--url', '-u', required=True, help='Target URL')
    parser.add_argument('--browser', '-b', default='chromium', choices=['chromium','firefox','webkit'], help='Browser engine')
    parser.add_argument('--no-headless', action='store_true', help='Run browser with visible UI (useful for debugging)')
    args = parser.parse_args()
    try:
        res = fetch_rendered_media(args.url, browser_name=args.browser, headless=(not args.no_headless))
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'error': str(e)}))


if __name__ == '__main__':
    main()
