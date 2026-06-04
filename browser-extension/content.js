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

  function normalizeOrigin(value) {
    return String(value || '').trim().replace(/\/$/, '')
  }

  async function getBackendOriginCandidates() {
    const defaults = ['http://localhost:8000', 'http://127.0.0.1:8000']
    try {
      if (chrome && chrome.storage && chrome.storage.sync) {
        const stored = await chrome.storage.sync.get(['backendOrigin'])
        const configured = normalizeOrigin(stored.backendOrigin)
        return [configured, ...defaults].map(normalizeOrigin).filter((origin, index, array) => origin && array.indexOf(origin) === index)
      }
    } catch (_error) {
      // fall through to defaults
    }
    return defaults.map(normalizeOrigin)
  }

  async function readResponseBody(response) {
    const text = await response.text()
    if (!text) {
      return { text: '', json: null }
    }
    try {
      return { text, json: JSON.parse(text) }
    } catch (_error) {
      return { text, json: null }
    }
  }

  async function postBookmark(url) {
    const originCandidates = await getBackendOriginCandidates()
    const tried = []

    for (const backendOrigin of originCandidates) {
      const endpoint = `${backendOrigin}/api/items/bookmark_fetch/`
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
          },
          body: JSON.stringify({ url }),
          signal: controller.signal,
        })

        const body = await readResponseBody(response)
        tried.push({ endpoint, ok: response.ok, status: response.status, body: body.json || body.text })

        return {
          ok: response.ok,
          status: response.status,
          body: body.json || body.text,
          endpoint,
          tried,
        }
      } catch (error) {
        const message = error && error.name === 'AbortError'
          ? `Timeout after ${REQUEST_TIMEOUT_MS}ms`
          : (error && error.message ? error.message : String(error))
        tried.push({ endpoint, ok: false, error: message })
      } finally {
        clearTimeout(timeoutId)
      }
    }

    const lastFailure = tried.length ? tried[tried.length - 1] : null
    const error = new Error(lastFailure && lastFailure.error ? lastFailure.error : 'Failed to contact backend')
    error.tried = tried
    throw error
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

  function sendBookmark(url) {
    showToast('Sending tweet URL to fanart_viewer…')
    const progressTimer = setTimeout(() => {
      showToast('Still working… fanart_viewer is fetching the tweet image.')
    }, 7000)

    if (pendingUrls.has(url)) {
      clearTimeout(progressTimer)
      showToast('Already sending this tweet…', 'error')
      return
    }
    pendingUrls.add(url)

    try {
      postBookmark(url)
        .then(result => {
          clearTimeout(progressTimer)
          if (result.ok) {
            const body = result.body || {}
            const status = body.status || 'saved'
            const count = typeof body.count !== 'undefined' ? body.count : ''
            showToast(count ? `fanart_viewer: ${status} (${count})` : `fanart_viewer: ${status}`)
            return
          }

          const body = result.body || {}
          const detail = body.detail || body.error || result.status || 'unknown error'
          const endpoint = result.endpoint ? `\n${result.endpoint}` : ''
          const status = typeof result.status !== 'undefined' ? `\nHTTP ${result.status}` : ''
          const tried = Array.isArray(result.tried) && result.tried.length
            ? `\ntried: ${result.tried.map(item => item.endpoint || item.error || 'unknown').join(' | ')}`
            : ''
          showToast(`fanart_viewer rejected the request:${status}${endpoint}\n${detail}${tried}`, 'error')
        })
        .catch(error => {
          clearTimeout(progressTimer)
          const message = error && error.message ? error.message : String(error)
          if (!/extension context invalidated/i.test(message)) {
            showToast(`Send failed: ${message}`, 'error')
          }
        })
        .finally(() => {
          pendingUrls.delete(url)
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

  showToast('fanart_viewer bookmark bridge loaded')
})()
