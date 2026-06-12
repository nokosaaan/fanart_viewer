# fanart_viewer Twitter Bookmark Bridge

This Chrome/Edge extension watches X/Twitter bookmark clicks and forwards the current tweet URL to the local fanart_viewer backend.

## How it works

- The content script runs on `x.com`, `twitter.com`, `www.pixiv.net`, and `poipiku.com(partly)`.
- When the bookmark button is clicked on a tweet page, it sends the current page URL to the background service worker.
- The background worker POSTs the URL to `http://localhost:8000/api/items/bookmark_fetch/`.
- fanart_viewer resolves the matching Item and stores fetched preview images in the DB.

## Install locally

1. Start fanart_viewer locally with Docker.
2. Open your browser extension page.
3. Enable Developer mode.
4. Load this folder as an unpacked extension.

## Optional backend URL override

If the backend is not on `http://localhost:8000`, set `backendOrigin` in `chrome.storage.sync` from the extension background context or edit `background.js`.
