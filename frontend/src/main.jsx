import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import { onSync } from './lib/crossWindowSync'

// Bridges cross-window broadcasts (see lib/crossWindowSync — used by the
// "open in a separate window" edit/fetch queue feature) back into ordinary
// same-window CustomEvents, so every existing listener (App.jsx's
// item-updated/item-deleted handlers, ScrollList's per-row
// item-preview-updated listener, ...) becomes cross-window-aware without
// any of them needing to change. Runs once per window/tab, regardless of
// whether it's the main app or a popped-out standalone queue window — both
// load this same entry point.
;['item-updated', 'item-preview-updated', 'item-deleted'].forEach(type => {
  onSync(type, detail => { try{ window.dispatchEvent(new CustomEvent(type, { detail })) }catch(_){} })
})

// Intercept all /api/ fetch calls and add Authorization header when a token exists.
// This is done once here so every component (App, ScrollList, PreviewPane, etc.)
// automatically sends auth without any per-file changes.
const _originalFetch = window.fetch.bind(window)
window.fetch = function (url, options = {}) {
  if (typeof url === 'string' && url.startsWith('/api/')) {
    const token = localStorage.getItem('fv_token')
    if (token) {
      const headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` }
      return _originalFetch(url, { ...options, headers })
    }
  }
  return _originalFetch(url, options)
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
