(() => {
  const DEDUPE_MS = 4000
  const REQUEST_TIMEOUT_MS = 20000
  const recentUrls = new Map()
  const pendingUrls = new Set()
  let toastTimer = null

  function ensureToast() {
    let toast = document.getElementById('fv-bookmark-toast')
    if (toast) {
      return toast
    }

    toast = document.createElement('div')
    toast.id = 'fv-bookmark-toast'
    toast.style.cssText = [
      'position:fixed',
      'right:16px',
      'bottom:16px',
      'z-index:2147483647',
      'max-width:320px',
      'padding:10px 12px',
      'border-radius:10px',
      'font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif',
      'color:#fff',
      'background:rgba(28,28,28,0.94)',
      'box-shadow:0 10px 30px rgba(0,0,0,0.28)',
      'display:none',
      'white-space:pre-wrap',
    ].join(';')
    document.documentElement.appendChild(toast)
    return toast
  }

  function showToast(message, kind) {
    const toast = ensureToast()
    if (!toast) {
      return
    }

    toast.style.background = kind === 'error' ? 'rgba(160,32,32,0.96)' : 'rgba(28,28,28,0.94)'
    toast.textContent = message
    toast.style.display = 'block'
    if (toastTimer) {
      clearTimeout(toastTimer)
    }
    toastTimer = setTimeout(() => {
      toast.style.display = 'none'
    }, 3200)
  }

  function normalizeUrl(value) {
    try {
      const parsed = new URL(value)
      parsed.hash = ''
      parsed.search = ''
      return parsed.toString()
    } catch (_error) {
      return value || ''
    }
  }

  function cleanupRecent() {
    const now = Date.now()
    for (const [url, timestamp] of recentUrls.entries()) {
      if (now - timestamp > DEDUPE_MS) {
        recentUrls.delete(url)
      }
    }
  }

  function getBookmarkActionElementFromPath(event) {
    const path = typeof event.composedPath === 'function' ? event.composedPath() : []
    for (const node of path) {
      if (!(node instanceof Element)) {
        continue
      }
      const testId = (node.getAttribute('data-testid') || '').toLowerCase()
      const label = `${node.getAttribute('aria-label') || ''} ${node.getAttribute('title') || ''} ${node.textContent || ''}`.trim().toLowerCase()
      if (testId === 'bookmark' || testId === 'unbookmark' || /bookmark/.test(label)) {
        return node
      }
    }

    if (event.target instanceof Element) {
      return event.target.closest('[data-testid="bookmark"], [data-testid="unbookmark"], button[aria-label*="Bookmark"], div[role="button"][aria-label*="Bookmark"], [aria-label*="Bookmark"]')
    }

    return null
  }

  function shouldTriggerBookmark(element) {
    if (!element) {
      return false
    }

    const testId = (element.getAttribute('data-testid') || '').toLowerCase()
    if (testId === 'bookmark' || testId === 'unbookmark') {
      return true
    }

    const label = `${element.getAttribute('aria-label') || ''} ${element.getAttribute('title') || ''} ${element.textContent || ''}`.trim().toLowerCase()
    return /bookmark/.test(label)
  }

  // Delegate the actual HTTP request to the background service worker.
  // Background scripts have access to chrome.cookies and can read the fv_auth
  // session cookie, which content scripts cannot do directly.
  function sendBookmark(url) {
    showToast('Sending to fanart_viewer…')
    const progressTimer = setTimeout(() => {
      showToast('Still working… fanart_viewer is fetching the image.')
    }, 7000)

    if (pendingUrls.has(url)) {
      clearTimeout(progressTimer)
      showToast('Already sending this URL…', 'error')
      return
    }
    pendingUrls.add(url)

    try {
      chrome.runtime.sendMessage({ type: 'FV_BOOKMARK_CLICKED', url }, response => {
        clearTimeout(progressTimer)
        pendingUrls.delete(url)

        if (chrome.runtime.lastError) {
          const msg = (chrome.runtime.lastError && chrome.runtime.lastError.message) || 'Extension error'
          if (!/extension context invalidated/i.test(msg)) {
            showToast(`Send failed: ${msg}`, 'error')
          }
          return
        }

        if (!response) {
          showToast('No response from extension background', 'error')
          return
        }

        // response.ok = background handled the message (true even for HTTP errors)
        // response.result.ok = the actual HTTP request succeeded
        if (!response.ok) {
          // postBookmark threw (network error / timeout)
          const tried = Array.isArray(response.tried) && response.tried.length
            ? `\ntried: ${response.tried.map(item => item.endpoint || item.error || 'unknown').join(' | ')}`
            : ''
          showToast(`Send failed: ${response.error || 'unknown error'}${tried}`, 'error')
          return
        }

        const result = response.result || {}
        if (result.ok) {
          const body = result.body || {}
          const status = body.status || 'saved'
          const count = typeof body.count !== 'undefined' ? body.count : ''
          showToast(count ? `fanart_viewer: ${status} (${count})` : `fanart_viewer: ${status}`)
          return
        }

        // HTTP error (4xx/5xx)
        const body = result.body || {}
        const detail = body.detail || body.error || result.status || 'unknown error'
        const hint = result.status === 401 ? '\n(fanart_viewer にログインしているか確認してください)' : ''
        const endpoint = result.endpoint ? `\n${result.endpoint}` : ''
        const httpStatus = typeof result.status !== 'undefined' ? `\nHTTP ${result.status}` : ''
        const tried = Array.isArray(result.tried) && result.tried.length
          ? `\ntried: ${result.tried.map(item => item.endpoint || item.error || 'unknown').join(' | ')}`
          : ''
        showToast(`fanart_viewer rejected the request:${httpStatus}${endpoint}\n${detail}${hint}${tried}`, 'error')
      })
    } catch (err) {
      clearTimeout(progressTimer)
      pendingUrls.delete(url)
      const message = err && err.message ? err.message : String(err)
      if (!/extension context invalidated/i.test(message)) {
        showToast(`Send failed: ${message}`, 'error')
      }
    }
  }

  function handlePotentialBookmark(event) {
    const element = getBookmarkActionElementFromPath(event)
    if (!element || !shouldTriggerBookmark(element)) {
      return
    }

    const currentUrl = normalizeUrl(location.href)
    if (!/\/status\/\d+/.test(currentUrl)) {
      return
    }

    cleanupRecent()
    const lastHit = recentUrls.get(currentUrl)
    const now = Date.now()
    if (lastHit && now - lastHit < DEDUPE_MS) {
      return
    }

    recentUrls.set(currentUrl, now)
    sendBookmark(currentUrl)
  }

  // --- Pixiv support ---

  function getPixivIllustId() {
    const match = location.pathname.match(/\/artworks\/(\d+)/)
    return match ? match[1] : null
  }

  function _isPixivBookmarkNode(node) {
    if (!(node instanceof Element)) return false
    const label = (node.getAttribute('aria-label') || '').trim()
    const title = (node.getAttribute('title') || '').trim()
    const gtm   = (node.getAttribute('data-gtm-value') || '').toLowerCase()
    const href  = node.getAttribute('href') || ''
    // Japanese: 前方一致（「ブックマーク」「ブックマーク済み」「ブックマーク済」など）
    if (/^ブックマーク/.test(label) || /^ブックマーク/.test(title)) return true
    // English: case-insensitive prefix match
    if (/^bookmarks?$/i.test(label) || /^bookmarks?$/i.test(title)) return true
    // Pixiv GTM analytics attribute
    if (gtm === 'bookmark_add' || gtm === 'bookmark_delete' || gtm === 'bookmark_remove') return true
    // Legacy bookmark_add.php link
    if (href.includes('bookmark_add.php')) return true
    return false
  }

  function getPixivBookmarkElementFromPath(event) {
    const path = typeof event.composedPath === 'function' ? event.composedPath() : []
    for (const node of path) {
      if (_isPixivBookmarkNode(node)) return node
    }
    if (event.target instanceof Element) {
      return event.target.closest(
        '[aria-label^="ブックマーク"], [title^="ブックマーク"], ' +
        '[aria-label="Bookmark"], [aria-label="Bookmarked"], ' +
        '[title="Bookmark"], [title="Bookmarked"], ' +
        '[data-gtm-value="bookmark_add"], [data-gtm-value="bookmark_delete"], ' +
        'a[href*="bookmark_add.php"]'
      )
    }
    return null
  }

  function shouldTriggerPixivBookmark(element) {
    return _isPixivBookmarkNode(element)
  }

  function handlePixivBookmark(event) {
    const illustId = getPixivIllustId()
    if (!illustId) return

    const element = getPixivBookmarkElementFromPath(event)
    if (!element || !shouldTriggerPixivBookmark(element)) return

    const artworkUrl = `https://www.pixiv.net/artworks/${illustId}`

    cleanupRecent()
    const now = Date.now()
    const lastHit = recentUrls.get(artworkUrl)
    if (lastHit && now - lastHit < DEDUPE_MS) return

    recentUrls.set(artworkUrl, now)
    sendBookmark(artworkUrl)
  }

  document.addEventListener('pointerdown', handlePotentialBookmark, true)
  document.addEventListener('mousedown', handlePotentialBookmark, true)
  document.addEventListener('click', handlePotentialBookmark, true)

  document.addEventListener('pointerdown', handlePixivBookmark, true)
  document.addEventListener('mousedown', handlePixivBookmark, true)
  document.addEventListener('click', handlePixivBookmark, true)

  // --- Poipiku support ---

  // Returns the canonical illustration URL if on a detail page, otherwise null.
  // Matches: /123/456.html  /123/456/  /123/456
  function getPoipikuIllustUrl() {
    const m = location.pathname.match(/^\/(\d+)\/(\d+)(?:\.html)?\/?$/)
    if (!m) return null
    return `https://poipiku.com/${m[1]}/${m[2]}.html`
  }

  function _isPoipikuBookmarkNode(node) {
    if (!(node instanceof Element)) return false
    const cls   = (node.className && typeof node.className === 'string' ? node.className : '').toLowerCase()
    const label = (node.getAttribute('aria-label') || '').toLowerCase()
    const title = (node.getAttribute('title') || '').toLowerCase()
    const text  = (node.textContent || '').trim()
    if (/bookmark|favorite|bookmarklist/i.test(cls)) return true
    if (/ブックマーク|お気に入り|保存/.test(label) || /bookmark/i.test(label)) return true
    if (/ブックマーク|お気に入り|保存/.test(title) || /bookmark/i.test(title)) return true
    if (/^(ブックマーク|お気に入り|保存する)$/.test(text)) return true
    return false
  }

  function getPoipikuBookmarkElementFromPath(event) {
    const path = typeof event.composedPath === 'function' ? event.composedPath() : []
    for (const node of path) {
      if (_isPoipikuBookmarkNode(node)) return node
    }
    if (event.target instanceof Element) {
      return event.target.closest(
        '[class*="BookMark"],[class*="Bookmark"],[class*="Favorite"],' +
        '[aria-label*="ブックマーク"],[aria-label*="bookmark" i],' +
        '[title*="ブックマーク"],[title*="bookmark" i]'
      )
    }
    return null
  }

  function handlePoipikuBookmark(event) {
    // Skip clicks on the injected FV button itself (handled by its own listener)
    if (event.target && event.target.id === 'fv-poipiku-btn') return

    const url = getPoipikuIllustUrl()
    if (!url) return

    const element = getPoipikuBookmarkElementFromPath(event)
    if (!element) return

    cleanupRecent()
    const now = Date.now()
    const lastHit = recentUrls.get(url)
    if (lastHit && now - lastHit < DEDUPE_MS) return

    recentUrls.set(url, now)
    sendBookmark(url)
  }

  // Inject a small floating "Save to FV" button on Poipiku detail pages.
  // This is a reliable fallback that works regardless of Poipiku's button structure.
  function injectPoipikuButton() {
    if (document.getElementById('fv-poipiku-btn')) return
    if (!getPoipikuIllustUrl()) return

    const btn = document.createElement('button')
    btn.id = 'fv-poipiku-btn'
    btn.textContent = '📌 FV'
    btn.title = 'Save to fanart_viewer'
    btn.style.cssText = [
      'position:fixed',
      'bottom:72px',
      'right:16px',
      'z-index:2147483646',
      'padding:6px 10px',
      'border-radius:8px',
      'border:none',
      'background:rgba(99,102,241,0.92)',
      'color:#fff',
      'font:bold 13px/1 system-ui,sans-serif',
      'cursor:pointer',
      'box-shadow:0 4px 12px rgba(0,0,0,0.3)',
      'user-select:none',
    ].join(';')

    btn.addEventListener('click', () => {
      const url = getPoipikuIllustUrl()
      if (!url) return
      cleanupRecent()
      const now = Date.now()
      const lastHit = recentUrls.get(url)
      if (lastHit && now - lastHit < DEDUPE_MS) return
      recentUrls.set(url, now)
      sendBookmark(url)
    })

    document.documentElement.appendChild(btn)
  }

  if (location.hostname === 'poipiku.com') {
    document.addEventListener('pointerdown', handlePoipikuBookmark, true)
    document.addEventListener('mousedown', handlePoipikuBookmark, true)
    document.addEventListener('click', handlePoipikuBookmark, true)

    // Inject button now and re-check after SPA-style navigation
    injectPoipikuButton()
    const _poipikuObserver = new MutationObserver(() => injectPoipikuButton())
    _poipikuObserver.observe(document.documentElement, { childList: true, subtree: false })
  }

  showToast('fanart_viewer bookmark bridge loaded')
})()
