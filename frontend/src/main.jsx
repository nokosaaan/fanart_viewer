import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

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
