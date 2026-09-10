import React, {useEffect, useState, useRef} from 'react'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'

const PANE_PAGE_SIZE = 50

export default function PreviewPane({open, onClose, readOnly, filteredItems, initialItemId}){
  const [items, setItems] = useState([])
  const allLoadedRef = useRef([]) // full unfiltered set fetched from API
  const [loading, setLoading] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(null)
  // Guards the initialItemId auto-jump below so it fires exactly once per
  // "open" — without it, a later `items` refresh (lazy pagination, an
  // item-preview-updated resync) would re-run the jump and yank the user
  // back to the originally-clicked item even after they'd navigated
  // elsewhere with prev()/next().
  const jumpedItemIdRef = useRef(null)
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

  function normalizeNext(next){
    if(!next) return null
    try{ const u = new URL(next); return u.pathname + (u.search || '') }catch(e){ return next }
  }

  function parsePageData(data){
    if(Array.isArray(data)) return { list: data, next: null }
    if(Array.isArray(data.results)) return { list: data.results, next: normalizeNext(data.next || null) }
    return { list: [], next: null }
  }

  // load items with optional pagination.
  // - replace=true, maxPages=Infinity (default): follows all API `next` links
  //   so the full set is loaded in one go — used to resync after a mutation
  //   (deleteCurrentPreview / clearAllPreviews) so we never end up with FEWER
  //   items loaded than before the resync.
  // - replace=true, maxPages=N: stops after N chunks and wires the remainder
  //   into nextPageUrl, so the existing scroll-triggered lazy loader (see the
  //   onScroll effect below) picks up the rest as the user scrolls — used for
  //   the initial "pane just opened" load so it doesn't have to fetch the
  //   entire dataset before showing anything.
  async function loadItems(url='/api/items/?page_size=1000', replace=true, maxPages=Infinity){
    try{
      if(replace){ setLoading(true); setNextPageUrl(null) }
      else { setLoadingMore(true) }

      if(replace){
        // fetch up to maxPages chunks and accumulate before updating state
        let accumulated = []
        let currentUrl = url
        let pages = 0
        while(currentUrl && mountedRef.current && pages < maxPages){
          const r = await fetch(currentUrl)
          if(!r.ok) break
          const { list, next } = parsePageData(await r.json())
          accumulated = accumulated.concat(
            list.filter(it => it && (it.has_preview===true || it.has_preview==='true'))
          )
          currentUrl = next
          pages += 1
        }
        if(!mountedRef.current) return accumulated
        allLoadedRef.current = accumulated
        let have = accumulated
        if(Array.isArray(filteredItems) && filteredItems.length > 0){
          const allowedIds = new Set(filteredItems.map(it => it.id))
          have = have.filter(it => allowedIds.has(it.id))
        }
        setItems(have)
        setNextPageUrl(currentUrl)
        // clamp panePageIndex so we never show an empty page after a reload
        const maxPage = Math.max(0, Math.ceil(have.length / PANE_PAGE_SIZE) - 1)
        setPanePageIndex(prev => Math.min(prev, maxPage))
        return have
      } else {
        // lazy append: load one more page
        const r = await fetch(url)
        if(!r.ok) return []
        const { list, next } = parsePageData(await r.json())
        const withPreview = list.filter(it => it && (it.has_preview===true || it.has_preview==='true'))
        if(!mountedRef.current) return withPreview
        allLoadedRef.current = (allLoadedRef.current || []).concat(withPreview)
        let have = allLoadedRef.current
        if(Array.isArray(filteredItems) && filteredItems.length > 0){
          const allowedIds = new Set(filteredItems.map(it => it.id))
          have = have.filter(it => allowedIds.has(it.id))
        }
        setItems(have)
        setNextPageUrl(next)
        return have
      }
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
    // Fast path: only the first chunk up front; the rest loads lazily as the
    // user scrolls (see the onScroll effect below), instead of chasing every
    // `next` link before the timeline can show anything.
    loadItems('/api/items/?page_size=1000', true, 1)
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

  // Jump straight to a specific item's enlarged view — set when the pane is
  // opened via ScrollList's preview thumbnail (see App.jsx's
  // openPreviewForItem) rather than the plain header-menu toggle. Matches
  // by item id, not array index — PreviewPane's own `items` is built from a
  // separate fetch+filter pipeline (see loadItems above) whose order/length
  // doesn't correspond to ScrollList's paginatedItems array position, so an
  // index handed in from there would point at the wrong item.
  useEffect(()=>{
    if(!open){ jumpedItemIdRef.current = null; return }
    if(initialItemId == null) return
    if(jumpedItemIdRef.current === initialItemId) return
    if(!items || items.length === 0) return
    const idx = items.findIndex(it => it && it.id === initialItemId)
    if(idx !== -1){
      jumpedItemIdRef.current = initialItemId
      setPanePageIndex(Math.floor(idx / PANE_PAGE_SIZE))
      setSelectedIndex(idx)
    }
  }, [open, initialItemId, items])

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
      // Up/Down move to the prev/next ITEM, matching the mouse wheel below
      // (deltaY drives next()/prev()) — Left/Right instead page through
      // THIS item's own images. Keeping both input methods on the same
      // axis for "next item" avoids the two disagreeing with each other.
      if(e.key==='ArrowUp') prev()
      if(e.key==='ArrowDown') next()
      if(e.key==='ArrowLeft'){ e.preventDefault(); prevPreviewImage() }
      if(e.key==='ArrowRight'){ e.preventDefault(); nextPreviewImage() }
    }
    window.addEventListener('keydown', onKey)
    return ()=> window.removeEventListener('keydown', onKey)
  }, [selectedIndex, items, previews])

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

  // Paging through THIS item's own images (e.g. a multi-page manga fetch)
  // is bound to Left/Right, not Up/Down — the mouse wheel already moves to
  // the prev/next ITEM on its own axis (deltaY, see handleWheel below), so
  // Up/Down mirrors that for the keyboard too (see onKey above) rather than
  // disagreeing with it. Left/Right was free precisely because Up/Down took
  // over "next item".
  function nextPreviewImage(){
    if(!previews || previews.length === 0) return
    selectPreviewIndex((currentPreviewIdxRef.current + 1) % previews.length)
  }

  function prevPreviewImage(){
    if(!previews || previews.length === 0) return
    selectPreviewIndex((currentPreviewIdxRef.current - 1 + previews.length) % previews.length)
  }

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
      notify('item-preview-updated', { id: it.id })
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
      notify('item-preview-updated', { id: it.id })
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
              {(() => {
                // The URL powering <img> below is itself already a raw-bytes
                // endpoint (ItemViewSet.preview: HttpResponse(img.data,
                // content_type=...), no wrapping page) — exactly the same
                // shape as an x.com pbs.twimg.com link. So "open the image
                // alone in a new tab, right-click, save" needs nothing new
                // server-side; just a link to this same URL, kept in sync
                // with whichever page of a multi-image item is on screen
                // (currentPreviewIdx) so it's never just "the first page"
                // regardless of what's actually being looked at.
                const previewImgSrc = (previews && previews.length>0)
                  ? `/api/items/${items[selectedIndex].id}/preview/?index=${currentPreviewIdx}`
                  : `/api/items/${items[selectedIndex].id}/preview/`
                return (
              <div className="modal-top">
                <div className="modal-main">
                  <img className="preview-modal-img" src={previewImgSrc} alt={(items[selectedIndex].titles && items[selectedIndex].titles[0])||items[selectedIndex].title||''} />
                </div>
                <div className="modal-meta">
                  <div className="preview-title">{(items[selectedIndex].titles && items[selectedIndex].titles[0]) || items[selectedIndex].titles || items[selectedIndex].title || ''}</div>
                  <div className="preview-artist">{items[selectedIndex].artist || ''}</div>
                  <a className="link-text" href={items[selectedIndex].link} target="_blank" rel="noreferrer" style={{display:'inline-flex', alignItems:'center', gap:6}}>
                    {(() => {
                      const platform = getPlatformIcon(items[selectedIndex].link)
                      return platform ? <img src={platform.icon} alt={platform.label} style={{width:16, height:16, borderRadius:3}} /> : null
                    })()}
                    Open source
                  </a>
                  <a className="link-text" href={previewImgSrc} target="_blank" rel="noreferrer" title="この画像だけを表示するページを新しいタブで開きます。右クリック→名前を付けて画像を保存、で保存できます" style={{display:'inline-flex', alignItems:'center', gap:6, marginTop:4}}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    画像を新しいタブで開く(保存用)
                  </a>
                  {!readOnly && (
                    <div style={{marginTop:12}}>
                      <button className="btn" style={{padding:'7px 10px', lineHeight:1, position:'relative'}} title="Delete this preview (only this one image)" onClick={deleteCurrentPreview} disabled={deleting}>
                        {deleting ? '…' : (
                          <>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>
                            {/* "1" badge distinguishes this from "Clear all previews" below — same
                                trash-can glyph otherwise, so the number is the only thing telling
                                "delete just this one image" and "delete every image" apart. */}
                            <span style={{position:'absolute', top:-4, right:-4, background:'#3b82f6', color:'#fff',
                              borderRadius:'50%', width:14, height:14, fontSize:10, lineHeight:'14px',
                              textAlign:'center', fontWeight:700}}>1</span>
                          </>
                        )}
                      </button>
                      <button className="btn" style={{marginLeft:8, padding:'7px 10px', lineHeight:1}} title="Clear all previews" onClick={clearAllPreviews} disabled={deleting}>
                        {deleting ? '…' : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>}
                      </button>
                    </div>
                  )}
                </div>
              </div>
                )
              })()}
              <div className="modal-timeline-wrap">
                {previews && previews.length>1 && (
                  <div className="modal-timeline-hint">←/→キーでこのアイテムの前後のページへ</div>
                )}
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
