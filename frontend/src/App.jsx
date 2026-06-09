import React, {useEffect, useState, useMemo} from 'react'
import SearchBar from './components/SearchBar'
import ScrollList from './components/ScrollList'
import PreviewPane from './components/PreviewPane'
import LoginScreen from './components/LoginScreen'
import CharacterGroupManager from './components/CharacterGroupManager'

function AppMain({ role, onLogout }){
  const readOnly = role === 'viewer'

  const [items, setItems] = useState([])
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState([])
  const [includeCP, setIncludeCP] = useState(false)
  const [includeR18, setIncludeR18] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [charGroupOpen, setCharGroupOpen] = useState(false)
  const [situationFilter, setSituationFilter] = useState('ALL')
  const [titleMissingOnly, setTitleMissingOnly] = useState(false)
  const [pageIndex, setPageIndex] = useState(0)
  const [pageInputVal, setPageInputVal] = useState('')
  const PAGE_SIZE = 50
  const [nextPageUrl, setNextPageUrl] = useState(null)
  const [loadingPages, setLoadingPages] = useState(false)
  const [backgroundIndexing, setBackgroundIndexing] = useState(false)
  // Fetch only the first page by default. For debugging you can call
  // `window.fetchAllItems()` from the console to fetch all pages.
  const fetchPage = async (url = '/api/items/') => {
    try{
      let fetchUrl = url
      try{
        if(typeof url === 'string' && (url.startsWith('http://') || url.startsWith('https://'))){
          const u = new URL(url)
          fetchUrl = u.pathname + (u.search || '')
        }
      }catch(_){
        fetchUrl = url
      }

      const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
      if(!r.ok){
        let bodyText = null
        try{ bodyText = await r.text() }catch(_){ bodyText = null }
        console.error('fetch failed', fetchUrl, r.status, bodyText)
        return
      }

      let data = null
      try{
        data = await r.json()
      }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        return
      }

      if(Array.isArray(data)){
        // not paginated: whole dataset returned
        setItems(uniqueById(data))
        setNextPageUrl(null)
      } else {
        const results = Array.isArray(data.results) ? data.results : []
        setItems(uniqueById(results))
        setNextPageUrl(data.next || null)
      }
    }catch(err){
      console.error('Failed to fetch items', err)
      setItems([])
      setNextPageUrl(null)
    }
  }

  // keep the original full-fetch routine available for debugging
  const fetchAll = async () => {
    try{
      const all = []
      let url = '/api/items/'
      while(url){
        let fetchUrl = url
        try{
          if(typeof url === 'string' && (url.startsWith('http://') || url.startsWith('https://'))){
            const u = new URL(url)
            fetchUrl = u.pathname + (u.search || '')
          }
        }catch(_){
          fetchUrl = url
        }

        const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
        if(!r.ok){
          let bodyText = null
          try{ bodyText = await r.text() }catch(_){ bodyText = null }
          console.error('fetch failed', fetchUrl, r.status, bodyText)
          break
        }
        let data = null
        try{
          data = await r.json()
        }catch(err){
          let raw = null
          try{ raw = await r.text() }catch(_){ raw = null }
          console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
          break
        }
        if(Array.isArray(data)){
          all.push(...data)
          break
        }
        const results = Array.isArray(data.results) ? data.results : []
        all.push(...results)
        url = data.next || null
      }
      setItems(uniqueById(all))
      setNextPageUrl(null)
    }catch(err){
      console.error('Failed to fetch items', err)
      setItems([])
      setNextPageUrl(null)
    }
  }

  useEffect(()=>{
    // Expose debug function on window for manual invocation in dev tools
    if(typeof window !== 'undefined'){
      window.fetchAllItems = fetchAll
    }
    
    // fetch the first page by default (guarded to avoid crash if function not available)
    if(typeof fetchPage === 'function'){
      fetchPage('/api/items/')
    }else{
      console.warn('fetchPage is not a function at mount — skipping initial fetch')
    }

    // Start background indexing (fetch all pages) so search works across entire dataset.
    // This runs asynchronously and won't block initial UI rendering.
    (async ()=>{
      try{
        setBackgroundIndexing(true)
        const all = await (async function(){
          const collected = []
          let url = '/api/items/'
          while(url){
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
              console.error('background fetch failed', fetchUrl, r.status, bodyText)
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
              break
            }
            const results = Array.isArray(data.results) ? data.results : []
            collected.push(...results)
            url = data.next || null
          }
          return collected
        })()

        if(Array.isArray(all) && all.length>0){
          setItems(uniqueById(all))
          setNextPageUrl(null)
          // clamp pageIndex to valid range after full dataset arrives
          setPageIndex(p=>{
            const maxPage = Math.max(0, Math.ceil(all.length / PAGE_SIZE) - 1)
            return Math.min(p, maxPage)
          })
        }
      }catch(err){
        console.error('Background indexing failed', err)
      }finally{
        setBackgroundIndexing(false)
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
    return ()=>{
      window.removeEventListener('item-deleted', onItemDeleted)
    }
  }, [])

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
      if(!includeR18 && (it.situation||'').toUpperCase()==='R18') return false
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
  }, [items, query, filters, includeCP, includeR18, situationFilter, titleMissingOnly])
  

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

  async function loadNextPage(){
    if(!nextPageUrl || loadingPages) return
    setLoadingPages(true)
    try{
      let fetchUrl = nextPageUrl
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
        return
      }
      let data = null
      try{ data = await r.json() }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        return
      }

      if(Array.isArray(data)){
        setItems(prev => uniqueById([...(Array.isArray(prev)?prev:[]), ...data]))
        setNextPageUrl(null)
      } else {
        const results = Array.isArray(data.results) ? data.results : []
        setItems(prev => uniqueById([...(Array.isArray(prev)?prev:[]), ...results]))
        setNextPageUrl(data.next || null)
      }
    }catch(err){
      console.error('Failed to load next page', err)
    }finally{
      setLoadingPages(false)
    }
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
          <button type="button" className="btn" onClick={()=>setCharGroupOpen(true)} style={{fontSize:12}}>キャラクターグループ</button>
          <button type="button" className="preview-toggle header-preview-btn" onClick={()=>setPreviewOpen(!previewOpen)}>
            Preview Timeline
          </button>
          {role !== 'none' && (
            <button type="button" className="btn" onClick={onLogout} style={{fontSize:12}}>ログアウト</button>
          )}
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
      />
      <ScrollList items={paginatedItems} readOnly={readOnly} />
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
                if(!isNaN(v)) setPageIndex(Math.max(0, Math.min(totalPages-1, v-1)))
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
          <button className="btn" onClick={()=>setPageIndex(p=>Math.min(totalPages-1, p+1))} disabled={pageIndex>=totalPages-1}>Next</button>
        </div>
      )}
      {previewOpen && (
        <React.Suspense fallback={<div className="preview-loading">Loading previews…</div>}>
          <PreviewPane open={previewOpen} onClose={()=>setPreviewOpen(false)} />
        </React.Suspense>
      )}
      {charGroupOpen && <CharacterGroupManager onClose={()=>setCharGroupOpen(false)} />}
    </div>
  )
}

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
  if (role === 'login') return <LoginScreen onLogin={handleLogin} />
  return <AppMain role={role} onLogout={handleLogout} />
}
