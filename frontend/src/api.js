// Prefer Vite-style env `VITE_BACKEND_URL`. For compatibility, also check
// `REACT_APP_API_URL` (some deploy UIs use that name) — Vite exposes it
// via import.meta.env as well when provided at build time.
const API_BASE = (import.meta && import.meta.env && (import.meta.env.VITE_BACKEND_URL || import.meta.env.REACT_APP_API_URL)) ? (import.meta.env.VITE_BACKEND_URL || import.meta.env.REACT_APP_API_URL) : ''

function normalizeBase(b){
  if(!b) return ''
  return b.endsWith('/') ? b.slice(0,-1) : b
}

const BASE = normalizeBase(API_BASE)

export function apiUrl(path){
  if(typeof path === 'string' && (path.startsWith('http://') || path.startsWith('https://'))){
    return path
  }
  const p = (typeof path === 'string' && path.startsWith('/')) ? path : ('/' + (path||''))
  return BASE ? BASE + p : p
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
