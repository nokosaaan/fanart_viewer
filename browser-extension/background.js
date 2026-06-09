const DEFAULT_BACKEND_ORIGIN = 'http://localhost:8000'
const REQUEST_TIMEOUT_MS = 20000

function normalizeOrigin(value) {
  return String(value || '').trim().replace(/\/$/, '')
}

async function getBackendOrigin() {
  try {
    const stored = await chrome.storage.sync.get(['backendOrigin'])
    const value = normalizeOrigin(stored.backendOrigin || DEFAULT_BACKEND_ORIGIN)
    return value || DEFAULT_BACKEND_ORIGIN
  } catch (_error) {
    return DEFAULT_BACKEND_ORIGIN
  }
}

// Read the auth token.
// Primary: chrome.storage.local (set by content_localhost.js reading the non-HttpOnly fv_ext cookie).
// Fallback: chrome.cookies API reading the HttpOnly fv_auth cookie directly.
async function readAuthToken(backendOrigin) {
  try {
    const stored = await chrome.storage.local.get('fv_auth_token')
    if (stored.fv_auth_token) return stored.fv_auth_token
  } catch (_) {}
  try {
    const cookie = await chrome.cookies.get({ url: backendOrigin + '/', name: 'fv_auth' })
    return cookie ? cookie.value : null
  } catch (_) {
    return null
  }
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
  const configuredOrigin = await getBackendOrigin()
  const originCandidates = [configuredOrigin, DEFAULT_BACKEND_ORIGIN, 'http://127.0.0.1:8000']
    .map(normalizeOrigin)
    .filter((origin, index, array) => origin && array.indexOf(origin) === index)

  const tried = []
  for (const backendOrigin of originCandidates) {
    const token = await readAuthToken(backendOrigin)
    const endpoint = `${backendOrigin}/api/items/bookmark_fetch/`
    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(new Error('Request timed out')), REQUEST_TIMEOUT_MS)
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers,
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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== 'FV_BOOKMARK_CLICKED') {
    return false
  }

  ;(async () => {
    try {
      const result = await postBookmark(message.url)
      sendResponse({ ok: true, result })
    } catch (error) {
      sendResponse({
        ok: false,
        error: error && error.message ? error.message : String(error),
        tried: error && error.tried ? error.tried : [],
      })
    }
  })()

  return true
})
