// Caches the item list metadata (titles/characters/tags/etc — no image
// bytes, those are fetched separately per-item) in localStorage so a reload
// can paint immediately from what was loaded last time, instead of showing
// a blank list until the network fetch completes. The cache is always
// treated as provisional: App.jsx still kicks off a real fetch right after
// restoring it and merges the fresh result in, so staleness self-heals
// within moments rather than needing any expiry logic here.
const CACHE_KEY = 'fv_items_cache_v1'

export function loadCachedItems(){
  try{
    const raw = localStorage.getItem(CACHE_KEY)
    if(!raw) return null
    const parsed = JSON.parse(raw)
    if(!parsed || !Array.isArray(parsed.items)) return null
    return parsed.items
  }catch(_){
    return null
  }
}

export function saveCachedItems(items){
  try{
    if(!Array.isArray(items) || items.length === 0){
      localStorage.removeItem(CACHE_KEY)
      return
    }
    localStorage.setItem(CACHE_KEY, JSON.stringify({ items, cachedAt: Date.now() }))
  }catch(_){
    // localStorage full/unavailable (private browsing, quota, etc.) — caching
    // is a pure optimization, so just skip it silently.
  }
}
