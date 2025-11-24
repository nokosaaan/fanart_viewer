from django.core.management.base import BaseCommand
from urllib.parse import urlparse
import re
import os

try:
    import requests
    HAVE_REQUESTS = True
except Exception:
    HAVE_REQUESTS = False


def _is_pixiv_host(hostname):
    return hostname and ('pixiv' in hostname or 'pximg' in hostname or 'i.pximg.net' in hostname)


def make_pixiv_original_candidate(url):
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        net = p.netloc
        if not _is_pixiv_host(net):
            return url
        path = p.path
        path = re.sub(r'^/c/\d+x\d+/img-master', '/img-master', path)
        path = path.replace('/img-master/', '/img-original/')
        path = re.sub(r'_master\d+(?=\.)', '', path)
        path = re.sub(r'(_p\d+)_master\d+', r'\1', path)
        return f"{p.scheme}://{p.netloc}{path}"
    except Exception:
        return url


def _fetch(uurl, extra_headers=None, timeout=12):
    headers = {'User-Agent': 'fanart-viewer-debug/1.0'}
    if extra_headers:
        headers.update(extra_headers)
    if HAVE_REQUESTS:
        try:
            r = requests.get(uurl, headers=headers, timeout=timeout, allow_redirects=True)
            ct = r.headers.get('content-type', '')
            return r.status_code, r.content, ct, dict(r.headers)
        except Exception as e:
            return None, None, None, {'error': str(e)}
    else:
        # fallback to urllib
        try:
            from urllib.request import Request, urlopen
            req = Request(uurl, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                info = resp.info()
                try:
                    ct = info.get_content_type()
                except Exception:
                    ct = info.get('Content-Type', '')
                data = resp.read()
                hdrs = dict(info.items())
                return resp.getcode(), data, ct, hdrs
        except Exception as e:
            return None, None, None, {'error': str(e)}


class Command(BaseCommand):
    help = 'Debug fetch for a URL (Pixiv-aware): tries original candidate and p0..p9 variants, prints status/content-type/size.'

    def add_arguments(self, parser):
        parser.add_argument('url', help='URL to fetch')
        parser.add_argument('--referer', help='Optional Referer header to add')
        parser.add_argument('--try-p-variants', action='store_true', help='Try p0..p9 variants when URL looks like _p0')
        parser.add_argument('--save-first', help='If set, save first successful image to this file path')

    def handle(self, *args, **options):
        url = options['url']
        referer = options.get('referer')
        try_p_variants = options.get('try_p_variants')
        save_first = options.get('save_first')

        self.stdout.write(self.style.NOTICE(f'Debug fetch for: {url}'))

        parsed = urlparse(url)
        extra = {}
        if referer:
            extra['Referer'] = referer
        elif _is_pixiv_host(parsed.netloc):
            extra['Referer'] = 'https://www.pixiv.net/'

        # First, if pixiv host, try transformed original candidate
        tried = []
        if _is_pixiv_host(parsed.netloc):
            orig = make_pixiv_original_candidate(url)
            if orig and orig != url:
                self.stdout.write(f'Trying pixiv original candidate: {orig}')
                tried.append(orig)

        # primary URL
        tried.append(url)

        first_saved = False
        for cand in tried:
            self.stdout.write(f'Fetching: {cand}')
            status, data, ct, hdrs = _fetch(cand, extra)
            if status is None:
                self.stdout.write(self.style.ERROR(f'Failed: {hdrs.get("error")}'))
                continue
            size = len(data) if data else 0
            is_image = (ct and str(ct).startswith('image'))
            self.stdout.write(f'  status={status} content-type={ct} size={size}')
            if save_first and not first_saved and is_image and size>0:
                try:
                    with open(save_first, 'wb') as f:
                        f.write(data)
                    self.stdout.write(self.style.SUCCESS(f'Saved first image to {save_first}'))
                    first_saved = True
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Failed saving file: {e}'))
            # If this is pixiv and we should try p-variants, and path contains _p0
            if try_p_variants and _is_pixiv_host(urlparse(cand).netloc) and re.search(r'_p0(_|\.)', urlparse(cand).path):
                base = re.sub(r'(_p)0', r'\1{}', cand)
                for i in range(0, 10):
                    purl = base.format(i)
                    purl = make_pixiv_original_candidate(purl)
                    self.stdout.write(f'  Trying page variant: {purl}')
                    st2, d2, ct2, h2 = _fetch(purl, {'Referer': 'https://www.pixiv.net/'})
                    if st2 is None:
                        self.stdout.write(self.style.ERROR(f'    Failed: {h2.get("error")}'))
                        continue
                    size2 = len(d2) if d2 else 0
                    is_img2 = (ct2 and str(ct2).startswith('image'))
                    self.stdout.write(f'    status={st2} content-type={ct2} size={size2}')
                    if save_first and not first_saved and is_img2 and size2>0:
                        try:
                            with open(save_first, 'wb') as f:
                                f.write(d2)
                            self.stdout.write(self.style.SUCCESS(f'Saved first image to {save_first}'))
                            first_saved = True
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f'Failed saving file: {e}'))

        self.stdout.write(self.style.SUCCESS('Debug fetch finished'))
