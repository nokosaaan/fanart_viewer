import React, {useEffect, useState, useMemo} from 'react'
import apiFetch from './api'
import SearchBar from './components/SearchBar'
import ScrollList from './components/ScrollList'
import PreviewPane from './components/PreviewPane'
import RestorePreviews from './components/RestorePreviews'

export default function App(){
  const [items, setItems] = useState([])
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState([])
  const [includeCP, setIncludeCP] = useState(false)
  const [includeR18, setIncludeR18] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [situationFilter, setSituationFilter] = useState('ALL')
  const [pageIndex, setPageIndex] = useState(0)
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

      const r = await apiFetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
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
        setItems(data)
        setNextPageUrl(null)
      } else {
        const results = Array.isArray(data.results) ? data.results : []
        setItems(results)
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

        const r = await apiFetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
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
      setItems(all)
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

            const r = await apiFetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
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
          setItems(all)
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
    return list.filter(it=>{
      if(!includeCP && (it.situation||'').toUpperCase()==='CP') return false
      if(!includeR18 && (it.situation||'').toUpperCase()==='R18') return false
      if(situationFilter && situationFilter!=='ALL'){
        if(((it.situation||'').toUpperCase()) !== situationFilter) return false
      }
      if(filters.length===0 && q==='') return true
      const hay = [ ...(it.titles||[]), ...(it.characters||[]), ...(it.tags||[]), it.artist, it.link ].join(' ').toLowerCase()
      const matchesQuery = q==='' || hay.includes(q)
      const matchesFilters = filters.every(f => hay.includes(f.toLowerCase()))
      return matchesQuery && matchesFilters
    })
  }, [items, query, filters, includeCP, situationFilter])
  

  // pagination over filtered results
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(()=>{
    // reset to first page if filters change
    setPageIndex(0)
  }, [query, filters, includeCP, includeR18, situationFilter])

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

      const r = await apiFetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
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
        setItems(prev => [...prev, ...data])
        setNextPageUrl(null)
      } else {
        const results = Array.isArray(data.results) ? data.results : []
        setItems(prev => [...prev, ...results])
        setNextPageUrl(data.next || null)
      }
    }catch(err){
      console.error('Failed to load next page', err)
    }finally{
      setLoadingPages(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Fanart Viewer</h1>
        <button type="button" className="preview-toggle header-preview-btn" onClick={()=>setPreviewOpen(!previewOpen)}>
          Preview Timeline
        </button>
        <RestorePreviews />
      
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
      />
      <ScrollList items={paginatedItems} />
      {nextPageUrl && (
        <div className="load-more" style={{margin:'12px 0'}}>
          <button className="btn" onClick={loadNextPage} disabled={loadingPages}>{loadingPages ? 'Loading…' : 'Load more pages'}</button>
          <span style={{marginLeft:12, color:'#666'}}>{backgroundIndexing ? 'Indexing all items in background…' : 'More pages available from server'}</span>
        </div>
      )}
      {filtered.length > PAGE_SIZE && (
        <div className="pagination-controls">
          <button className="btn" onClick={()=>setPageIndex(p=>Math.max(0, p-1))} disabled={pageIndex===0}>Prev</button>
          <span style={{margin:'0 12px'}}>Page {pageIndex+1} / {totalPages} — {filtered.length} results</span>
          <button className="btn" onClick={()=>setPageIndex(p=>Math.min(totalPages-1, p+1))} disabled={pageIndex>=totalPages-1}>Next</button>
        </div>
      )}
      {previewOpen && (
        <React.Suspense fallback={<div className="preview-loading">Loading previews…</div>}>
          <PreviewPane open={previewOpen} onClose={()=>setPreviewOpen(false)} />
        </React.Suspense>
      )}
    </div>
  )
}
