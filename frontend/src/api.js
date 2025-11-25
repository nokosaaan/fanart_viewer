// Determine backend base URL from multiple possible sources, in this order:
// 1. runtime global `window.__ENV && window.__ENV.VITE_BACKEND_URL` (set by public/env.js)
// 2. Vite build-time `import.meta.env.VITE_BACKEND_URL` or `import.meta.env.REACT_APP_API_URL`
// 3. <meta name="backend-url" content="https://..."> in index.html
// 4. empty (fall back to relative paths)
function readMetaBackendUrl(){
  try{
    const m = document.querySelector('meta[name="backend-url"]')
    return m && m.content ? m.content : ''
  }catch(_){ return '' }
}

function getRuntimeEnv(){
  try{
    if(window && window.__ENV && window.__ENV.VITE_BACKEND_URL) return window.__ENV.VITE_BACKEND_URL
  }catch(_){ }
  return ''
}

let BUILD_ENV = ''
try{
  BUILD_ENV = (import.meta && import.meta.env) ? (import.meta.env.VITE_BACKEND_URL || import.meta.env.REACT_APP_API_URL || '') : ''
}catch(_){ BUILD_ENV = '' }

const API_BASE = getRuntimeEnv() || BUILD_ENV || readMetaBackendUrl() || ''

function normalizeBase(b){
  if(!b) return ''
  // remove trailing slash
  return b.replace(/\/+$/, '')
}

const BASE = normalizeBase(API_BASE)

export function apiUrl(path){
  if(typeof path === 'string' && (path.startsWith('http://') || path.startsWith('https://'))){
    return path
  }
  const p = (typeof path === 'string' && path.startsWith('/')) ? path : ('/' + (path||''))
  if(!BASE) return p
  return BASE + p
}

export default function apiFetch(path, opts){
  let url = path
  try{
    if(typeof path === 'string' && (path.startsWith('http://') || path.startsWith('https://'))){
      url = path
    } else {
      url = apiUrl(path)
    }
  }catch(_){ url = path }
  return fetch(url, opts)
}
