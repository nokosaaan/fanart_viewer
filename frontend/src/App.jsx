import React, {useEffect, useState, useMemo, useRef} from 'react'
import SearchBar from './components/SearchBar'
import ScrollList from './components/ScrollList'
import PreviewPane from './components/PreviewPane'
import LoginScreen from './components/LoginScreen'
import CharacterGroupManager from './components/CharacterGroupManager'
import BackupManager from './components/BackupManager'
import FetchQueueManager from './components/FetchQueueManager'
import EditQueueManager from './components/EditQueueManager'
import HeaderMenu from './components/HeaderMenu'
import { loadCachedItems, saveCachedItems } from './lib/itemsCache'

function AppMain({ role, onLogout }){
  const readOnly = role === 'viewer'

  // Lazy-initialize from whatever was cached last session so the list paints
  // immediately on reload instead of sitting empty until the network fetch
  // below resolves. The mount effect still fetches fresh data right away and
  // merges it in, so this is purely a "show something now" optimization —
  // never the final source of truth.
  const [items, setItems] = useState(() => loadCachedItems() || [])
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState([])
  const [includeCP, setIncludeCP] = useState(false)
  const [includeR18, setIncludeR18] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [charGroupOpen, setCharGroupOpen] = useState(false)
  const [backupOpen, setBackupOpen] = useState(false)
  // Mailbox-style queue: fetching an item's image candidates (ScrollList)
  // appends here instead of popping an inline modal, so accidentally
  // clicking outside a modal backdrop can no longer discard results that
  // would otherwise require re-fetching. Reviewed/processed from the header
  // "取得キュー" button (FetchQueueManager). In-memory only — cleared on reload.
  const [fetchQueue, setFetchQueue] = useState([])
  const [fetchQueueOpen, setFetchQueueOpen] = useState(false)
  const [editQueueOpen, setEditQueueOpen] = useState(false)
  function enqueueFetchResult({ itemId, images }){
    setFetchQueue(prev => [...prev, { id: `${itemId}-${Date.now()}-${Math.random().toString(36).slice(2,7)}`, itemId, images, fetchedAt: Date.now() }])
  }
  function removeFromFetchQueue(entryId){
    setFetchQueue(prev => prev.filter(e => e.id !== entryId))
  }
  const [situationFilter, setSituationFilter] = useState('ALL')
  const [titleMissingOnly, setTitleMissingOnly] = useState(false)
  const [pageIndex, setPageIndex] = useState(0)
  const [pageInputVal, setPageInputVal] = useState('')
  const PAGE_SIZE = 50
  const [nextPageUrl, setNextPageUrl] = useState(null)
  const [loadingPages, setLoadingPages] = useState(false)
  const [backgroundIndexing, setBackgroundIndexing] = useState(false)
  // Kept in sync with nextPageUrl but readable synchronously mid-async-function,
  // so a loop of several loadNextPage() calls in a row (see goToNextPage) doesn't
  // keep re-reading the stale value captured when the loop started.
  const nextPageUrlRef = useRef(null)
  useEffect(()=>{ nextPageUrlRef.current = nextPageUrl }, [nextPageUrl])
  // Same idea for the raw loaded item count — used to decide how many more
  // backend pages a page-jump needs, without waiting on filtered/totalPages
  // (a memo, so it can't be read fresh mid-loop either).
  const itemsCountRef = useRef(items.length)
  // Guards the search-triggered full background load so it only ever starts once.
  const fullIndexStartedRef = useRef(false)
  const INITIAL_PAGES = 3

  // Fetch backend pages starting at `startUrl`, following `next` up to
  // `maxPages` times (Infinity = fetch everything). Shared by the initial
  // fast-path load, the on-demand full index, and the debug fetchAll().
  async function fetchItemsPages(startUrl, maxPages = Infinity){
    const collected = []
    let url = startUrl
    let pages = 0
    while(url && pages < maxPages){
      let fetchUrl = url
      try{
        if(typeof url === 'string' && (url.startsWith('http://') || url.startsWith('https://'))){
          const u = new URL(url)
          fetchUrl = u.pathname + (u.search || '')
        }
      }catch(_){ fetchUrl = url }

      const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
      if(!r.ok){
        let bodyText = null
        try{ bodyText = await r.text() }catch(_){ bodyText = null }
        console.error('fetch failed', fetchUrl, r.status, bodyText)
        break
      }
      let data = null
      try{ data = await r.json() }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        break
      }

      if(Array.isArray(data)){
        collected.push(...data)
        url = null
        break
      }
      const results = Array.isArray(data.results) ? data.results : []
      collected.push(...results)
      url = data.next || null
      pages += 1
    }
    return { items: collected, nextUrl: url }
  }

  // keep the original full-fetch routine available for debugging
  const fetchAll = async () => {
    try{
      const { items: all } = await fetchItemsPages('/api/items/')
      const unique = uniqueById(all)
      itemsCountRef.current = unique.length
      setItems(unique)
      setNextPageUrl(null)
    }catch(err){
      console.error('Failed to fetch items', err)
      itemsCountRef.current = 0
      setItems([])
      setNextPageUrl(null)
    }
  }

  useEffect(()=>{
    // Expose debug function on window for manual invocation in dev tools
    if(typeof window !== 'undefined'){
      window.fetchAllItems = fetchAll
    }

    // Load only the first few backend pages up front for a fast initial paint.
    // The rest is fetched lazily as the user pages forward (goToNextPage) or
    // once a search/filter needs the full dataset (see the effect below).
    // `items` may already hold last session's cached list at this point (see
    // the useState initializer above) — merge rather than overwrite so a slow
    // or failed fetch doesn't blank out something we already had to show.
    ;(async ()=>{
      try{
        const { items: initial, nextUrl } = await fetchItemsPages('/api/items/', INITIAL_PAGES)
        setItems(prev => {
          const merged = uniqueById([...initial, ...(Array.isArray(prev) ? prev : [])])
          itemsCountRef.current = merged.length
          return merged
        })
        setNextPageUrl(nextUrl)
      }catch(err){
        // Leave `items`/`nextPageUrl` as-is (whatever the cache restored, or
        // empty if there was none) rather than blanking the list on a
        // transient network error.
        console.error('Failed to fetch items — keeping cached list, if any', err)
      }
    })()
    // Listen for item-deleted events to remove items from local state
    function onItemDeleted(ev){
      try{
        const id = ev && ev.detail && ev.detail.id
        if(id == null) return
        setItems(prev => Array.isArray(prev) ? prev.filter(it=> it.id !== id && it.pk !== id && it.external_id !== id) : prev)
      }catch(e){/* ignore */}
    }
    window.addEventListener('item-deleted', onItemDeleted)
    // Merge an edited item (from EditFields) back into the shared items list.
    // Without this, only the editing row's own local display state updated —
    // the underlying item object here stayed stale, so reopening the editor
    // later showed pre-edit values and the user had to redo the whole edit.
    function onItemUpdated(ev){
      try{
        const updated = ev && ev.detail && ev.detail.item
        if(!updated || updated.id == null) return
        setItems(prev => Array.isArray(prev) ? prev.map(it => it.id === updated.id ? { ...it, ...updated } : it) : prev)
      }catch(e){/* ignore */}
    }
    window.addEventListener('item-updated', onItemUpdated)
    return ()=>{
      window.removeEventListener('item-deleted', onItemDeleted)
      window.removeEventListener('item-updated', onItemUpdated)
    }
  }, [])

  // Keep the on-disk cache in sync with whatever's loaded, so the next
  // reload can paint from it immediately (see the useState initializer
  // above). Debounced so rapid-fire updates (e.g. the background full-index
  // fetch appending page after page) don't serialize the whole list on every
  // single page.
  useEffect(()=>{
    const t = setTimeout(()=>{ saveCachedItems(items) }, 500)
    return ()=> clearTimeout(t)
  }, [items])

  // Search/filters only see whatever's been loaded so far. The first time the
  // user actually searches, fetch the rest of the dataset in the background
  // (once) so results aren't silently incomplete.
  useEffect(()=>{
    const searching = query.trim() !== '' || filters.length > 0
    if(!searching || !nextPageUrl || fullIndexStartedRef.current) return
    fullIndexStartedRef.current = true
    let cancelled = false
    ;(async ()=>{
      setBackgroundIndexing(true)
      try{
        const { items: rest, nextUrl } = await fetchItemsPages(nextPageUrl)
        if(cancelled) return
        setItems(prev => {
          const merged = uniqueById([...(Array.isArray(prev)?prev:[]), ...rest])
          itemsCountRef.current = merged.length
          return merged
        })
        nextPageUrlRef.current = nextUrl
        setNextPageUrl(nextUrl)
      }catch(err){
        console.error('Background indexing failed', err)
      }finally{
        if(!cancelled) setBackgroundIndexing(false)
      }
    })()
    return ()=>{ cancelled = true }
  }, [query, filters, nextPageUrl])

  const suggestions = useMemo(()=>{
    const set = new Set()
    const list = Array.isArray(items) ? items : (items && Array.isArray(items.results) ? items.results : [])
    list.forEach(it=>{
      if(Array.isArray(it.titles)) {
        it.titles.forEach(t=> set.add(t))
      } else if(typeof it.titles === 'string' && it.titles) {
        set.add(it.titles)
      }

      if(Array.isArray(it.characters)) {
        it.characters.forEach(c=> set.add(c))
      } else if(typeof it.characters === 'string' && it.characters) {
        set.add(it.characters)
      }

      if(Array.isArray(it.tags)) {
        it.tags.forEach(tag=> set.add(tag))
      } else if(typeof it.tags === 'string' && it.tags) {
        set.add(it.tags)
      }
    })
    return Array.from(set)
  }, [items])

  const filtered = useMemo(()=>{
    const q = query.trim().toLowerCase()
    const list = Array.isArray(items) ? items : (items && Array.isArray(items.results) ? items.results : [])

    function hasAnyTitle(it){
      if(!it) return false
      if(Array.isArray(it.titles)){
        return it.titles.some(t => String(t || '').trim().length > 0)
      }
      if(typeof it.titles === 'string'){
        return it.titles.trim().length > 0
      }
      return false
    }

    return list.filter(it=>{
      if(!includeCP && (it.situation||'').toUpperCase()==='CP') return false
      if((readOnly || !includeR18) && (it.situation||'').toUpperCase()==='R18') return false
      if(situationFilter && situationFilter!=='ALL'){
        if(((it.situation||'').toUpperCase()) !== situationFilter) return false
      }
      if(titleMissingOnly && hasAnyTitle(it)) return false
      if(filters.length===0 && q==='') return true
      const hay = [ ...(it.titles||[]), ...(it.characters||[]), ...(it.tags||[]), it.artist, it.link ].join(' ').toLowerCase()
      const matchesQuery = q==='' || hay.includes(q)
      const matchesFilters = filters.every(f => hay.includes(f.toLowerCase()))
      return matchesQuery && matchesFilters
    })
  }, [items, query, filters, includeCP, includeR18, situationFilter, titleMissingOnly, readOnly])
  

  // pagination over filtered results
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(()=>{
    // reset to first page if filters change
    setPageIndex(0)
  }, [query, filters, includeCP, includeR18, situationFilter, titleMissingOnly])

  const paginatedItems = useMemo(()=>{
    const start = pageIndex * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, pageIndex])

  function addFilter(value){
    if(!value) return
    setFilters(prev=> prev.includes(value)? prev : [...prev, value])
    setQuery('')
  }

  function removeFilter(value){
    setFilters(prev=> prev.filter(p=>p!==value))
  }

  // Returns true if a page was actually fetched. Reads/writes nextPageUrlRef
  // (not just the nextPageUrl state) so a caller can await this in a loop —
  // e.g. goToNextPage() below — and see the updated url on the next iteration
  // instead of the value from whenever the loop started.
  async function loadNextPage(){
    const url = nextPageUrlRef.current
    if(!url || loadingPages) return false
    setLoadingPages(true)
    try{
      let fetchUrl = url
      try{
        if(typeof fetchUrl === 'string' && (fetchUrl.startsWith('http://') || fetchUrl.startsWith('https://'))){
          const u = new URL(fetchUrl)
          fetchUrl = u.pathname + (u.search || '')
        }
      }catch(_){ /* leave fetchUrl as-is */ }

      const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
      if(!r.ok){
        let bodyText = null
        try{ bodyText = await r.text() }catch(_){ bodyText = null }
        console.error('fetch failed', fetchUrl, r.status, bodyText)
        return false
      }
      let data = null
      try{ data = await r.json() }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        return false
      }

      const results = Array.isArray(data) ? data : (Array.isArray(data.results) ? data.results : [])
      const newNext = Array.isArray(data) ? null : (data.next || null)
      setItems(prev => {
        const merged = uniqueById([...(Array.isArray(prev)?prev:[]), ...results])
        itemsCountRef.current = merged.length
        return merged
      })
      nextPageUrlRef.current = newNext
      setNextPageUrl(newNext)
      return true
    }catch(err){
      console.error('Failed to load next page', err)
      return false
    }finally{
      setLoadingPages(false)
    }
  }

  // Fetch more backend pages (via loadNextPage, one at a time) until either
  // enough raw items are loaded for `targetIndex`, the backend runs out of
  // pages, or `maxFetches` is hit. Uses itemsCountRef/nextPageUrlRef (not
  // filtered/totalPages) so the loop condition is re-checked fresh each
  // iteration instead of once against a stale memo.
  async function ensureItemsFor(targetIndex, maxFetches){
    let fetches = 0
    while((targetIndex+1) * PAGE_SIZE > itemsCountRef.current && nextPageUrlRef.current && fetches < maxFetches){
      const ok = await loadNextPage()
      if(!ok) break
      fetches++
    }
  }

  // Advance to the next client-side page, transparently fetching more backend
  // pages first if we're at the edge of what's currently loaded.
  async function goToNextPage(){
    await ensureItemsFor(pageIndex+1, 5)
    setPageIndex(p => p+1)
  }

  // Jump to an arbitrary page number, fetching ahead if it's beyond what's
  // currently loaded (higher cap since a manual jump can span further).
  async function goToPage(targetIndex){
    await ensureItemsFor(targetIndex, 20)
    const maxKnownPage = Math.max(0, Math.ceil(itemsCountRef.current / PAGE_SIZE) - 1)
    setPageIndex(Math.max(0, Math.min(targetIndex, maxKnownPage)))
  }

  // helper: ensure array of items is unique by `id` preserving first occurrence order
  function uniqueById(arr){
    if(!Array.isArray(arr)) return []
    const seen = new Set()
    const out = []
    for(const it of arr){
      const id = it && (it.id || it._id || it.pk || it.external_id)
      if(id == null){
        out.push(it)
        continue
      }
      if(seen.has(id)) continue
      seen.add(id)
      out.push(it)
    }
    return out
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Fanart Viewer</h1>
        <div style={{display:'flex', alignItems:'center', gap:8}}>
          {readOnly && <span style={{fontSize:12, color:'#94a3b8', border:'1px solid #334155', borderRadius:4, padding:'2px 8px'}}>view only</span>}
          <HeaderMenu items={[
            { label: 'Preview Timeline', onClick: () => setPreviewOpen(p => !p), active: previewOpen },
            ...(readOnly ? [] : [
              { divider: true },
              { label: 'キャラクターグループ', onClick: () => setCharGroupOpen(true) },
              { label: '取得キュー', onClick: () => setFetchQueueOpen(true), badge: fetchQueue.length > 0 ? fetchQueue.length : null },
              { label: '編集キュー', onClick: () => setEditQueueOpen(true) },
              { label: 'バックアップ', onClick: () => setBackupOpen(true) },
            ]),
            ...(role !== 'none' ? [
              { divider: true },
              { label: 'ログアウト', onClick: onLogout },
            ] : []),
          ]} />
        </div>
      </header>
      <SearchBar
        query={query}
        setQuery={setQuery}
        suggestions={suggestions}
        onAddSuggestion={addFilter}
        filters={filters}
        onRemoveFilter={removeFilter}
        includeCP={includeCP}
        setIncludeCP={setIncludeCP}
        includeR18={includeR18}
        setIncludeR18={setIncludeR18}
        previewOpen={previewOpen}
        setPreviewOpen={setPreviewOpen}
        situationFilter={situationFilter}
        setSituationFilter={setSituationFilter}
        titleMissingOnly={titleMissingOnly}
        setTitleMissingOnly={setTitleMissingOnly}
        readOnly={readOnly}
      />
      <ScrollList items={paginatedItems} readOnly={readOnly} onEnqueueFetch={enqueueFetchResult} />
      {nextPageUrl && (
        <div className="load-more" style={{margin:'12px 0'}}>
          <button className="btn" onClick={loadNextPage} disabled={loadingPages}>{loadingPages ? 'Loading…' : 'Load more pages'}</button>
          <span style={{marginLeft:12, color:'#666'}}>{backgroundIndexing ? 'Indexing all items in background…' : 'More pages available from server'}</span>
        </div>
      )}
      {filtered.length > PAGE_SIZE && (
        <div className="pagination-controls">
          <button className="btn" onClick={()=>setPageIndex(p=>Math.max(0, p-1))} disabled={pageIndex===0}>Prev</button>
          <span style={{margin:'0 8px'}}>Page</span>
          <input
            type="number"
            min={1}
            max={totalPages}
            value={pageInputVal !== '' ? pageInputVal : pageIndex+1}
            onChange={e=>setPageInputVal(e.target.value)}
            onKeyDown={e=>{
              if(e.key==='Enter'){
                const v = parseInt(pageInputVal, 10)
                if(!isNaN(v)) goToPage(v-1)
                setPageInputVal('')
                e.target.blur()
              } else if(e.key==='Escape'){
                setPageInputVal('')
                e.target.blur()
              }
            }}
            onBlur={()=>setPageInputVal('')}
            style={{width:56, textAlign:'center', padding:'2px 4px'}}
          />
          <span style={{margin:'0 8px'}}>/ {totalPages} — {filtered.length} results</span>
          <button className="btn" onClick={goToNextPage} disabled={pageIndex>=totalPages-1 && !nextPageUrl}>Next</button>
        </div>
      )}
      {previewOpen && (
        <React.Suspense fallback={<div className="preview-loading">Loading previews…</div>}>
          <PreviewPane open={previewOpen} onClose={()=>setPreviewOpen(false)} readOnly={readOnly} filteredItems={filtered} />
        </React.Suspense>
      )}
      {charGroupOpen && <CharacterGroupManager onClose={()=>setCharGroupOpen(false)} />}
      {backupOpen && <BackupManager onClose={()=>setBackupOpen(false)} />}
      {fetchQueueOpen && (
        <FetchQueueManager
          queue={fetchQueue}
          onRemove={removeFromFetchQueue}
          onClose={()=>setFetchQueueOpen(false)}
          currentPageItems={paginatedItems}
          onEnqueueFetch={enqueueFetchResult}
        />
      )}
      {editQueueOpen && <EditQueueManager onClose={()=>setEditQueueOpen(false)} />}
    </div>
  )
}

const ADMIN_PATH = import.meta.env.VITE_ADMIN_PATH || ''

// Thin auth wrapper — handles login state and renders AppMain once authenticated.
export default function App() {
  const [role, setRole] = useState(null) // null=checking, 'login', 'admin', 'viewer', 'none'

  useEffect(() => {
    fetch('/api/auth/')
      .then(r => r.json())
      .then(j => {
        if (!j.auth_required) { setRole('none'); return }
        const saved = localStorage.getItem('fv_role')
        if (saved) {
          fetch('/api/items/?page_size=1').then(r => {
            if (r.ok) setRole(saved)
            else { localStorage.removeItem('fv_token'); localStorage.removeItem('fv_role'); setRole('login') }
          }).catch(() => setRole('login'))
        } else {
          setRole('login')
        }
      })
      .catch(() => setRole('none'))
  }, [])

  function handleLogin(newRole) { setRole(newRole) }

  function handleLogout() {
    localStorage.removeItem('fv_token')
    localStorage.removeItem('fv_role')
    setRole('login')
  }

  if (role === null) return null
  if (role === 'login') {
    const isAdminLogin = Boolean(ADMIN_PATH) && window.location.pathname === `/${ADMIN_PATH}`
    return <LoginScreen onLogin={handleLogin} isAdmin={isAdminLogin} />
  }
  return <AppMain role={role} onLogout={handleLogout} />
}
