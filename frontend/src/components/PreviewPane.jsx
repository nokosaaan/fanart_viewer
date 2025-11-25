import React, {useEffect, useState, useRef} from 'react'
import apiFetch, { apiUrl } from '../api'

export default function PreviewPane({open, onClose}){
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(null)
  const [previews, setPreviews] = useState([]) // per-item preview list
  const [currentPreviewIdx, setCurrentPreviewIdx] = useState(0)
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
      const r = await apiFetch(url)
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
      }
      const have = list.filter(it => it && (it.has_preview===true || it.has_preview==='true'))
      if(!mountedRef.current) return have
      if(replace){
        setItems(have)
      } else {
        setItems(prev => (prev || []).concat(have))
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
    loadItems('/api/items/?page_size=1000', true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

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
    if(idx===null || idx===undefined) return
    const it = items[idx]
    if(!it) return
    try{
      const r = await apiFetch(`/api/items/${it.id}/previews/`)
      if(!r.ok) return
      const j = await r.json()
      if(Array.isArray(j)){
        setPreviews(j)
        setCurrentPreviewIdx(0)
      }
    }catch(e){
      console.error('failed to load previews', e)
    }
  }

  useEffect(()=>{
    loadPreviewsForIndex(selectedIndex)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex, items])

  async function deleteCurrentPreview(){
    if(selectedIndex===null || !items[selectedIndex]) return
    const it = items[selectedIndex]
    const idx = currentPreviewIdx
    const ok = window.confirm('このプレビューを削除しますか？ この操作は取り消せません。')
    if(!ok) return
    try{
      const r = await apiFetch(`/api/items/${it.id}/previews/${idx}/`, { method: 'DELETE' })
      if(!r.ok){
        const j = await r.json().catch(()=>null)
        console.warn('delete preview failed', j)
        alert('削除に失敗しました。コンソールを確認してください。')
        return
      }
      // refresh previews and the item list so thumbnails update
      await loadPreviewsForIndex(selectedIndex)
      await loadItems('/api/items/?page_size=1000', true)
      alert('プレビューを削除しました。')
    }catch(e){ console.error(e); alert('削除に失敗しました') }
  }

  async function deleteItem(){
    if(selectedIndex===null || !items[selectedIndex]) return
    const it = items[selectedIndex]
    const ok = window.confirm('このアイテムをデータベースから完全に削除しますか？ この操作は取り消せません。')
    if(!ok) return
    try{
      const r = await apiFetch(`/api/items/${it.id}/`, { method: 'DELETE' })
      if(!r.ok){
        const j = await r.json().catch(()=>null)
        console.warn('delete item failed', j)
        alert('削除に失敗しました。コンソールを確認してください。')
        return
      }
      // refresh list and close modal
      await loadItems('/api/items/?page_size=1000', true)
      setSelectedIndex(null)
      try{ window.dispatchEvent(new CustomEvent('item-deleted', { detail: { id: it.id } })) }catch(e){}
      alert('Item deleted.')
    }catch(e){ console.error(e); alert('削除に失敗しました') }
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
            {items.map((it, idx) => (
              <div className="preview-item" key={it.id}>
                <button className="preview-thumb-btn" onClick={()=>openLarge(idx)}>
                  <img className="preview-thumb" src={apiUrl(`/api/items/${it.id}/preview/?index=0`)} alt={it.title||''} />
                </button>
                <div className="preview-meta">
                  <div className="preview-title">{(it.titles && it.titles[0]) || it.titles || it.title || ''}</div>
                  <div className="preview-artist">{it.artist || ''}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {selectedIndex!==null && items[selectedIndex] && (
        <div className="preview-modal-backdrop" onClick={()=>setSelectedIndex(null)}>
          <div className="preview-modal">
              {/* Left/right full-height edge zones for consistent click areas */}
            <div className="modal-edge modal-edge-left" onClick={prev} aria-label="Previous" />
            <div className="modal-edge modal-edge-right" onClick={next} aria-label="Next" />

              {/* Close button (top-right) */}
              <button className="modal-close" onClick={()=>setSelectedIndex(null)} aria-label="Close">✕</button>

            <div className="modal-content" onClick={e=>e.stopPropagation()}>
              <div className="modal-main">
                <img className="preview-modal-img" src={
                  (previews && previews.length>0)
                    ? apiUrl(`/api/items/${items[selectedIndex].id}/preview/?index=${currentPreviewIdx}`)
                    : apiUrl(`/api/items/${items[selectedIndex].id}/preview/`)
                } alt={(items[selectedIndex].titles && items[selectedIndex].titles[0])||items[selectedIndex].title||''} />
                <div className="modal-timeline-wrap">
                  <div className="modal-timeline">
                    {previews && previews.length>0 ? previews.map(p=> (
                      <img key={p.index} src={apiUrl(`/api/items/${items[selectedIndex].id}/preview/?index=${p.index}`)} alt={`preview-${p.index}`} className={currentPreviewIdx===p.index? 'timeline-thumb selected':'timeline-thumb'} onClick={()=>setCurrentPreviewIdx(p.index)} />
                    )) : (
                      <div className="timeline-empty">No previews</div>
                    )}
                  </div>
                </div>
              </div>
              <div className="modal-meta">
                <div className="preview-title">{(items[selectedIndex].titles && items[selectedIndex].titles[0]) || items[selectedIndex].titles || items[selectedIndex].title || ''}</div>
                <div className="preview-artist">{items[selectedIndex].artist || ''}</div>
                <a className="link-text" href={items[selectedIndex].link} target="_blank" rel="noreferrer">Open source</a>
                <div style={{marginTop:12, display:'flex', gap:8}}>
                  {/* Delete actions are intentionally disabled to prevent destructive
                      database operations from the public web UI. To re-enable,
                      uncomment the buttons below. */}
                  { 
                  <button className="btn" style={{background:'#ef4444'}} onClick={deleteCurrentPreview}>Delete preview</button>
                  /*
                  <button className="btn" style={{background:'#b91c1c'}} onClick={deleteItem}>Delete item</button>
                  */ }
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
