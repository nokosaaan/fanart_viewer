import React, {useEffect, useState, useRef} from 'react'

const PANE_PAGE_SIZE = 50

export default function PreviewPane({open, onClose, readOnly, filteredItems}){
  const [items, setItems] = useState([])
  const allLoadedRef = useRef([]) // full unfiltered set fetched from API
  const [loading, setLoading] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(null)
  const [panePageIndex, setPanePageIndex] = useState(0)
  const [previews, setPreviews] = useState([]) // per-item preview list
  const [currentPreviewIdx, setCurrentPreviewIdx] = useState(0)
  const [selectedPreviewId, setSelectedPreviewId] = useState(null)
  const currentPreviewIdxRef = useRef(0)
  const mountedRef = useRef(false)
  const [nextPageUrl, setNextPageUrl] = useState(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const previewPaneRef = useRef(null)

  useEffect(()=>{
    mountedRef.current = true
    return ()=>{ mountedRef.current = false }
  }, [])

  // load items with optional pagination; for the preview timeline request a
  // large page so newly-saved previews (which may be far down the list)
  // are included. This is a pragmatic client-side improvement; a server-side
  // filter would be preferable for very large datasets.
  async function loadItems(url='/api/items/?page_size=1000', replace=true){
    try{
      if(replace){ setLoading(true); setNextPageUrl(null) }
      else { setLoadingMore(true) }
      const r = await fetch(url)
      if(!r.ok) return []
      const data = await r.json()
      let list = []
      let next = null
      if(Array.isArray(data)){
        list = data
        next = null
      } else if(Array.isArray(data.results)){
        list = data.results
        next = data.next || null
        // normalize `next` when backend returns an absolute URL that may
        // reference an internal container hostname (e.g. http://web:8000)
        try{
          if(next){
            const u = new URL(next)
            next = u.pathname + (u.search || '')
          }
        }catch(e){
          // if parsing fails, leave `next` as-is
        }
      }
      const withPreview = list.filter(it => it && (it.has_preview===true || it.has_preview==='true'))
      if(!mountedRef.current) return withPreview
      if(replace){
        allLoadedRef.current = withPreview
      } else {
        allLoadedRef.current = (allLoadedRef.current || []).concat(withPreview)
      }
      // Apply the same item-level filters as the main list (e.g. R18 hidden for viewer)
      let have = allLoadedRef.current
      if(Array.isArray(filteredItems) && filteredItems.length > 0){
        const allowedIds = new Set(filteredItems.map(it => it.id))
        have = have.filter(it => allowedIds.has(it.id))
      }
      if(replace){
        setItems(have)
      } else {
        setItems(have)
      }
      setNextPageUrl(next)
      return have
    }catch(e){
      console.error('Failed to load preview items', e)
      if(mountedRef.current && replace) setItems([])
      return []
    }finally{
      if(replace){ if(mountedRef.current) setLoading(false) }
      else { if(mountedRef.current) setLoadingMore(false) }
    }
  }

  useEffect(()=>{
    if(!open) return
    setPanePageIndex(0)
    loadItems('/api/items/?page_size=1000', true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Re-apply filter when filteredItems changes (e.g. search/situation filter toggled while pane is open)
  useEffect(()=>{
    if(!open) return
    let have = allLoadedRef.current || []
    if(Array.isArray(filteredItems) && filteredItems.length > 0){
      const allowedIds = new Set(filteredItems.map(it => it.id))
      have = have.filter(it => allowedIds.has(it.id))
    }
    setItems(have)
    setSelectedIndex(null)
    setPanePageIndex(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredItems])

  // close preview pane when clicking outside it (but not when clicking the modal)
  useEffect(()=>{
    if(!open) return
    function onDocMouseDown(e){
      const pane = previewPaneRef.current
      if(!pane) return
      const modal = document.querySelector('.preview-modal-backdrop')
      // if click is inside modal, do not close the pane
      if(modal && modal.contains(e.target)) return
      if(!pane.contains(e.target)){
        try{ onClose && onClose() }catch(_){ }
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return ()=> document.removeEventListener('mousedown', onDocMouseDown)
  }, [open, onClose])

  useEffect(()=>{
    function onKey(e){
      if(selectedIndex===null) return
      if(e.key==='Escape') setSelectedIndex(null)
      if(e.key==='ArrowLeft') prev()
      if(e.key==='ArrowRight') next()
    }
    window.addEventListener('keydown', onKey)
    return ()=> window.removeEventListener('keydown', onKey)
  }, [selectedIndex, items])

  // wheel navigation: accumulate deltas to avoid accidental small scrolls
  const wheelAccRef = useRef(0)
  const lastNavRef = useRef(0)
  useEffect(()=>{
    function handleWheel(e){
      if(selectedIndex===null) return
      // prefer vertical wheel (deltaY) but accept deltaX as well
      const delta = e.deltaY || e.deltaX || 0
      wheelAccRef.current += delta
      const now = Date.now()
      const THRESH = 80 // threshold to trigger nav
      const COOLDOWN = 180 // ms between navigations
      if(Math.abs(wheelAccRef.current) > THRESH && (now - lastNavRef.current) > COOLDOWN){
        if(wheelAccRef.current > 0) next()
        else prev()
        wheelAccRef.current = 0
        lastNavRef.current = now
      }
    }
    // attach to window to capture wheel inside modal
    window.addEventListener('wheel', handleWheel, {passive: true})
    return ()=> window.removeEventListener('wheel', handleWheel)
  }, [selectedIndex, items])

  function openLarge(i){
    setSelectedIndex(i)
  }

  // when selectedIndex changes, fetch the preview list for that item
  // load previews for a specific selected index (reusable)
  async function loadPreviewsForIndex(idx){
    setPreviews([])
    setCurrentPreviewIdx(0)
    setSelectedPreviewId(null)
    currentPreviewIdxRef.current = 0
    if(idx===null || idx===undefined) return
    const it = items[idx]
    if(!it) return
    try{
      const r = await fetch(`/api/items/${it.id}/previews/`)
      if(!r.ok) return
      const j = await r.json()
      if(Array.isArray(j)){
        setPreviews(j)
        setCurrentPreviewIdx(0)
        setSelectedPreviewId((j[0] && j[0].id) || null)
        currentPreviewIdxRef.current = 0
      }
    }catch(e){
      console.error('failed to load previews', e)
    }
  }

  useEffect(()=>{
    loadPreviewsForIndex(selectedIndex)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex, items])

  useEffect(()=>{
    currentPreviewIdxRef.current = currentPreviewIdx
  }, [currentPreviewIdx])

  const [deleting, setDeleting] = useState(false)

  function selectPreviewIndex(idx){
    currentPreviewIdxRef.current = idx
    setCurrentPreviewIdx(idx)
    try{
      const selected = previews && previews.length>idx ? previews[idx] : null
      setSelectedPreviewId((selected && selected.id) || null)
    }catch(e){
      setSelectedPreviewId(null)
    }
  }

  async function deleteCurrentPreview(){
    if(selectedIndex===null) return
    const it = items[selectedIndex]
    if(!it) return
    const pid = selectedPreviewId || ((previews && previews[currentPreviewIdx]) ? previews[currentPreviewIdx].id : null)
    const ok = window.confirm('Delete this preview image? This cannot be undone.')
    if(!ok) return
    setDeleting(true)
    try{
      // prefer deleting by stable DB id when available
      let resp
      if(pid){
        resp = await fetch(`/api/items/${it.id}/previews/id/${pid}/`, {method: 'DELETE'})
      } else {
        const idx = currentPreviewIdxRef.current
        resp = await fetch(`/api/items/${it.id}/previews/${idx}/`, {method: 'DELETE'})
      }
      if(!resp.ok){
        const j = await resp.json().catch(()=>({}));
        alert('Failed to delete preview: '+(j.detail||j.error||resp.status))
        return
      }
      // reload previews for this item and refresh items list
      await loadPreviewsForIndex(selectedIndex)
      try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
      alert('Preview deleted.')
    }catch(e){ console.error(e); alert('Failed to delete preview') }
    finally{ setDeleting(false) }
  }

  async function clearAllPreviews(){
    if(selectedIndex===null) return
    const it = items[selectedIndex]
    if(!it) return
    const ok = window.confirm('Clear all previews for this item? This will remove all preview images.')
    if(!ok) return
    setDeleting(true)
    try{
      const resp = await fetch(`/api/items/${it.id}/previews/`, {method: 'DELETE'})
      if(!resp.ok){ const j = await resp.json().catch(()=>({})); alert('Failed to clear previews: '+(j.detail||j.error||resp.status)); return }
      // refresh items and previews
      await loadItems('/api/items/?page_size=1000', true)
      setPreviews([])
      setCurrentPreviewIdx(0)
      try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
      alert('All previews cleared.')
    }catch(e){ console.error(e); alert('Failed to clear previews') }
    finally{ setDeleting(false) }
  }

  // listen for external updates (e.g. when a preview is fetched elsewhere in the UI)
  useEffect(()=>{
    function onItemPreviewUpdated(e){
      const id = e && e.detail && e.detail.id
      // refresh the first page so thumbnails / has_preview flags are up-to-date
      if(open){
        const openedId = (selectedIndex !== null && items[selectedIndex]) ? items[selectedIndex].id : null
        loadItems('/api/items/?page_size=1000', true).then((loaded)=>{
          if(!mountedRef.current) return
          // if the modal was open on the updated item, reload its previews using the new index
          if(id!=null && openedId === id){
            const newIndex = (loaded || []).findIndex(it => it && it.id === id)
            if(newIndex !== -1){
              loadPreviewsForIndex(newIndex)
            } else if(selectedIndex !== null){
              loadPreviewsForIndex(selectedIndex)
            }
          }
        }).catch(()=>{})
      }
    }
    window.addEventListener('item-preview-updated', onItemPreviewUpdated)
    return ()=> window.removeEventListener('item-preview-updated', onItemPreviewUpdated)
  }, [open, selectedIndex, items])

  // lazy-load more items when the preview pane is scrolled near the bottom
  useEffect(()=>{
    const el = previewPaneRef.current
    if(!el) return
    function onScroll(){
      if(!nextPageUrl || loadingMore) return
      const scrollBottom = el.scrollTop + el.clientHeight
      if(el.scrollHeight - scrollBottom < 240){
        loadItems(nextPageUrl, false)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return ()=> el.removeEventListener('scroll', onScroll)
  }, [nextPageUrl, loadingMore, previewPaneRef.current])

  function prev(){
    if(selectedIndex===null) return
    setSelectedIndex((selectedIndex - 1 + items.length) % items.length)
  }

  function next(){
    if(selectedIndex===null) return
    setSelectedIndex((selectedIndex + 1) % items.length)
  }

  return (
    <>
      <div className="preview-pane" ref={previewPaneRef}>
        <div className="preview-header">
          <strong>Preview Timeline</strong>
          <div className="preview-controls">
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="preview-body">
          {loading && <div className="preview-loading">Loading…</div>}
          {!loading && items.length===0 && (
            <div className="preview-empty">No previews available</div>
          )}
          <div className="preview-list">
            {items.slice(panePageIndex*PANE_PAGE_SIZE, (panePageIndex+1)*PANE_PAGE_SIZE).map((it, localIdx) => {
              const globalIdx = panePageIndex * PANE_PAGE_SIZE + localIdx
              return (
                <div className="preview-item" key={it.id}>
                  <button className="preview-thumb-btn" onClick={()=>openLarge(globalIdx)}>
                    <img className="preview-thumb" src={`/api/items/${it.id}/preview/?index=0`} alt={it.title||''} />
                  </button>
                  <div className="preview-meta">
                    <div className="preview-item-id">#{it.id}</div>
                    <div className="preview-title">{(it.titles && it.titles[0]) || it.titles || it.title || ''}</div>
                    <div className="preview-artist">{it.artist || ''}</div>
                  </div>
                </div>
              )
            })}
          </div>
          {items.length > PANE_PAGE_SIZE && (
            <div className="pane-pagination">
              <button className="btn" onClick={()=>setPanePageIndex(p=>Math.max(0,p-1))} disabled={panePageIndex===0}>Prev</button>
              <span>Page</span>
              <input
                type="number"
                min={1}
                max={Math.ceil(items.length/PANE_PAGE_SIZE)}
                value={panePageIndex+1}
                onChange={e=>{
                  const v = parseInt(e.target.value,10)
                  if(!isNaN(v)) setPanePageIndex(Math.max(0, Math.min(Math.ceil(items.length/PANE_PAGE_SIZE)-1, v-1)))
                }}
                style={{width:48, textAlign:'center'}}
              />
              <span>/ {Math.ceil(items.length/PANE_PAGE_SIZE)}</span>
              <button className="btn" onClick={()=>setPanePageIndex(p=>Math.min(Math.ceil(items.length/PANE_PAGE_SIZE)-1,p+1))} disabled={panePageIndex>=Math.ceil(items.length/PANE_PAGE_SIZE)-1}>Next</button>
            </div>
          )}
        </div>
      </div>

      {selectedIndex!==null && items[selectedIndex] && (
        <div className="preview-modal-backdrop" onClick={()=>setSelectedIndex(null)}>
          <div className="preview-modal">
              {/* Left/right full-height edge zones for consistent click areas */}
            <div className="modal-edge modal-edge-left" onClick={e=>{e.stopPropagation(); prev()}} aria-label="Previous" />
            <div className="modal-edge modal-edge-right" onClick={e=>{e.stopPropagation(); next()}} aria-label="Next" />

              {/* Close button (top-right) */}
              <button className="modal-close" onClick={()=>setSelectedIndex(null)} aria-label="Close">✕</button>

            <div className="modal-content" onClick={e=>e.stopPropagation()}>
              <div className="modal-top">
                <div className="modal-main">
                  <img className="preview-modal-img" src={
                    (previews && previews.length>0)
                      ? `/api/items/${items[selectedIndex].id}/preview/?index=${currentPreviewIdx}`
                      : `/api/items/${items[selectedIndex].id}/preview/`
                  } alt={(items[selectedIndex].titles && items[selectedIndex].titles[0])||items[selectedIndex].title||''} />
                </div>
                <div className="modal-meta">
                  <div className="preview-title">{(items[selectedIndex].titles && items[selectedIndex].titles[0]) || items[selectedIndex].titles || items[selectedIndex].title || ''}</div>
                  <div className="preview-artist">{items[selectedIndex].artist || ''}</div>
                  <a className="link-text" href={items[selectedIndex].link} target="_blank" rel="noreferrer">Open source</a>
                  {!readOnly && (
                    <div style={{marginTop:12}}>
                      <button className="btn" onClick={deleteCurrentPreview} disabled={deleting}>{deleting ? 'Deleting...' : 'Delete This Preview'}</button>
                      <button className="btn" style={{marginLeft:8}} onClick={clearAllPreviews} disabled={deleting}>{deleting ? 'Processing...' : 'Clear All Previews'}</button>
                    </div>
                  )}
                </div>
              </div>
              <div className="modal-timeline-wrap">
                <div className="modal-timeline">
                  {previews && previews.length>0 ? previews.map(p=> (
                    <img key={p.index} src={`/api/items/${items[selectedIndex].id}/preview/?index=${p.index}`} alt={`preview-${p.index}`} className={currentPreviewIdx===p.index? 'timeline-thumb selected':'timeline-thumb'} onClick={()=>selectPreviewIndex(p.index, p.id)} />
                  )) : (
                    <div className="timeline-empty">No previews</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
