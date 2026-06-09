// Runs on the fanart_viewer web UI (localhost:8000 or Cloudflare tunnel domain).
// Reads the auth token from localStorage (fv_token) or the non-HttpOnly cookie
// (fv_ext) and caches it in chrome.storage.local so the background service
// worker can include it as Authorization: Bearer in API requests.
;(function syncExtToken() {
  try {
    // Prefer localStorage token (always present after login)
    const token = localStorage.getItem('fv_token')
    if (token) {
      chrome.storage.local.set({ fv_auth_token: token, fv_auth_token_ts: Date.now() })
      return
    }
    // Fallback: non-HttpOnly cookie fv_ext (set alongside fv_auth on login)
    const match = document.cookie.match(/(?:^|;\s*)fv_ext=([^;]+)/)
    if (match) {
      chrome.storage.local.set({
        fv_auth_token: decodeURIComponent(match[1]),
        fv_auth_token_ts: Date.now(),
      })
    }
  } catch (_) {}
})()
