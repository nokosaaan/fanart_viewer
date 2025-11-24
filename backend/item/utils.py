import os
import requests

def fetch_twitter_media_url(tweet_url):
    # extract tweet id from URL
    # examples of tweet_url: <https://twitter.com/user/status/1957412640629907791>
    parts = tweet_url.rstrip('/').split('/')
    tweet_id = parts[-1]
    bearer = os.environ.get('TW_BEARER')
    if not bearer:
        raise RuntimeError('TW_BEARER not set')

    api = f'https://api.twitter.com/2/tweets/{tweet_id}'
    params = {
        'expansions': 'attachments.media_keys',
        'media.fields': 'type,url,preview_image_url'
    }
    headers = {'Authorization': f'Bearer {bearer}'}
    r = requests.get(api, headers=headers, params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    media = j.get('includes', {}).get('media', [])
    # prefer photo type and url
    for m in media:
        if m.get('type') == 'photo' and m.get('url'):
            return m['url']
        if m.get('preview_image_url'):
            return m['preview_image_url']
    # fallback: try first media url-like field
    if media:
        for key in ('url', 'preview_image_url', 'media_url_https'):
            if media[0].get(key):
                return media[0][key]
    return None