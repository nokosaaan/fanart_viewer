import React, {useEffect, useState, useRef, useMemo} from 'react'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'

const PANE_PAGE_SIZE = 50

// This pane used to run its own separate fetch pipeline (its own
// /api/items/?page_size=1000 pagination, following every `next` link before
// showing anything) just to re-derive "which items have a preview" — but
// `filteredItems` (App.jsx's own `filtered`) is ALREADY the complete,
// up-to-date, fully-loaded set of items the main list itself is showing
// (same search/situation/other filters), so re-fetching it here was pure
// duplicated network work, and following every pagination page before
// rendering anything is exactly why opening this pane used to take a long
// time. Deriving `previewItems` as a plain in-memory filter of
// `filteredItems` makes opening instant (no network round-trip at all for
// the list itself) and keeps "still respects the current search/situation
// filters" for free, since that's exactly what `filteredItems` already is.
//
// Staying in sync with the rest of the app also comes for free: any
// `item-preview-updated`/`item-updated` event anywhere (a fetch queue, a
// manual upload, ScrollList's own preview actions, or this pane's own
// delete below) is already handled by App.jsx's own listeners, which patch
// its `items` state in place — that flows straight through to `filtered` /
// `filteredItems` / `previewItems` on its own, without this component
// needing its OWN separate listener or reload (it used to have one; see
// git history for the older, more complex version this replaced).
export default function PreviewPane({open, onClose, readOnly, filteredItems, initialItemId}){
  const previewItems = useMemo(() => (
    (filteredItems || []).filter(it => it && (it.has_preview === true || it.has_preview === 'true'))
  ), [filteredItems])

  // Which item's enlarged view is open, tracked by STABLE id rather than a
  // plain positional index into `previewItems` — that array gets a new
  // reference (and can reorder/shrink/grow) on essentially any preview
  // mutation app-wide (see the module comment above), and a positional
  // index into a freshly-replaced array can point at the wrong item, or
  // (worse) silently render nothing once the array shrinks, closing the
  // enlarged view entirely right when the user was mid-review of exactly
  // that item. Deriving `selectedIndex` by re-locating this id in the
  // CURRENT `previewItems` on every render keeps the same item open across
  // any such reference change, and only actually closes when that item is
  // genuinely no longer present (its last preview was just deleted, or it
  // stopped matching the active filter) — which is the one case where
  // closing is actually correct.
  const [selectedItemId, setSelectedItemId] = useState(null)
  const selectedIndex = useMemo(() => {
    if (selectedItemId == null) return null
    const idx = previewItems.findIndex(it => it && it.id === selectedItemId)
    return idx === -1 ? null : idx
  }, [previewItems, selectedItemId])
  // Guards the initialItemId auto-jump below so it fires exactly once per
  // "open" — without it, a later `previewItems` refresh would re-run the
  // jump and yank the user back to the originally-clicked item even after
  // they'd navigated elsewhere with prev()/next().
  const jumpedItemIdRef = useRef(null)
  const [panePageIndex, setPanePageIndex] = useState(0)
  const [previews, setPreviews] = useState([]) // per-item preview list
  const [currentPreviewIdx, setCurrentPreviewIdx] = useState(0)
  const [selectedPreviewId, setSelectedPreviewId] = useState(null)
  // Ids deleted from THIS item's own filmstrip but not yet reflected by a
  // fresh fetch — see deleteCurrentPreview. Deleting one image used to
  // immediately refetch/replace `previews`, which reindexes every later
  // image down by one and visually shifts every thumbnail after the
  // deleted one into a new slot — data-wise the right one was gone, but
  // with several images on screen at once it reads as "the wrong (last)
  // one disappeared", since that's the slot whose content visibly changed.
  // Marking the id here instead, and rendering that one slot as a
  // "deleted" placeholder without touching any other slot's position,
  // makes it visually unambiguous which image was actually removed. This
  // resets (and the array actually compacts) the next time this item's
  // previews are freshly loaded — see loadPreviewsForItem.
  const [deletedPreviewIds, setDeletedPreviewIds] = useState(() => new Set())
  const currentPreviewIdxRef = useRef(0)
  const mountedRef = useRef(false)
  const previewPaneRef = useRef(null)

  useEffect(()=>{
    mountedRef.current = true
    return ()=>{ mountedRef.current = false }
  }, [])

  // Reset selection only on a genuine open transition (closed -> open), not
  // on every `previewItems` reference change while already open — that
  // distinction is exactly what used to close the modal out from under the
  // user after an unrelated (or even their own) preview mutation elsewhere
  // caused `filteredItems` to get a new array identity. The initialItemId
  // jump effect below runs right after this one and overrides it when a
  // specific item was requested.
  const wasOpenRef = useRef(false)
  useEffect(()=>{
    if(open && !wasOpenRef.current){
      setSelectedItemId(null)
      setPanePageIndex(0)
    }
    wasOpenRef.current = open
  }, [open])

  // Keep the current page in range as previewItems grows/shrinks (a filter
  // change, a background item-list load finishing, etc.) instead of
  // silently landing on a page with nothing on it.
  useEffect(()=>{
    const maxPage = Math.max(0, Math.ceil(previewItems.length / PANE_PAGE_SIZE) - 1)
    setPanePageIndex(prev => Math.min(prev, maxPage))
  }, [previewItems.length])

  // Jump straight to a specific item's enlarged view — set when the pane is
  // opened via ScrollList's preview thumbnail (see App.jsx's
  // openPreviewForItem) rather than the plain header-menu toggle. Matches
  // by item id, not array index — this pane's own `previewItems` ordering
  // doesn't necessarily correspond to ScrollList's paginatedItems array
  // position, so an index handed in from there would point at the wrong
  // item.
  useEffect(()=>{
    if(!open){ jumpedItemIdRef.current = null; return }
    if(initialItemId == null) return
    if(jumpedItemIdRef.current === initialItemId) return
    if(!previewItems || previewItems.length === 0) return
    const idx = previewItems.findIndex(it => it && it.id === initialItemId)
    if(idx !== -1){
      jumpedItemIdRef.current = initialItemId
      setPanePageIndex(Math.floor(idx / PANE_PAGE_SIZE))
      setSelectedItemId(initialItemId)
    }
  }, [open, initialItemId, previewItems])

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
      if(e.key==='Escape') setSelectedItemId(null)
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
  }, [selectedIndex, previewItems, previews])

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
  }, [selectedIndex, previewItems])

  function openLarge(i){
    const it = previewItems[i]
    if(it) setSelectedItemId(it.id)
  }

  // Loads the previews for whichever item `selectedItemId` names, looked up
  // fresh in `previewItems` at call time — keyed ONLY on selectedItemId
  // (see the effect below), not on previewItems itself, so a mere array
  // reference/position change (the item's id staying the same) never
  // re-triggers this fetch; only actually selecting a different item does.
  async function loadPreviewsForItem(item){
    setPreviews([])
    setCurrentPreviewIdx(0)
    setSelectedPreviewId(null)
    setDeletedPreviewIds(new Set())
    currentPreviewIdxRef.current = 0
    if(!item) return
    try{
      const r = await fetch(`/api/items/${item.id}/previews/`)
      if(!r.ok) return
      const j = await r.json()
      if(!mountedRef.current) return
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
    const it = selectedItemId != null ? previewItems.find(x => x && x.id === selectedItemId) : null
    loadPreviewsForItem(it)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedItemId])

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
    let idx = currentPreviewIdxRef.current
    for(let step=0; step<previews.length; step++){
      idx = (idx + 1) % previews.length
      if(!deletedPreviewIds.has(previews[idx].id)){ selectPreviewIndex(previews[idx].index); return }
    }
  }

  function prevPreviewImage(){
    if(!previews || previews.length === 0) return
    let idx = currentPreviewIdxRef.current
    for(let step=0; step<previews.length; step++){
      idx = (idx - 1 + previews.length) % previews.length
      if(!deletedPreviewIds.has(previews[idx].id)){ selectPreviewIndex(previews[idx].index); return }
    }
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

  // Marks `deletedId` as gone WITHOUT touching any other entry's position —
  // see deletedPreviewIds' own comment for why. Also moves the "currently
  // viewed" image off the now-deleted one, onto the nearest still-alive
  // one (preferring the next one after it, wrapping around, falling back
  // to whichever is first) — so the viewer isn't left staring at a
  // placeholder for the very image they just deleted.
  function markPreviewDeletedLocally(deletedId){
    const deletedArrIdx = previews.findIndex(p => p.id === deletedId)
    const alive = previews.filter(p => p.id !== deletedId && !deletedPreviewIds.has(p.id))
    setDeletedPreviewIds(prev => {
      const next = new Set(prev)
      next.add(deletedId)
      return next
    })
    if(alive.length === 0){
      setCurrentPreviewIdx(0)
      setSelectedPreviewId(null)
      return
    }
    const forward = alive.find(p => previews.findIndex(x => x.id === p.id) > deletedArrIdx)
    selectPreviewIndex((forward || alive[0]).index)
  }

  async function deleteCurrentPreview(){
    if(selectedIndex===null) return
    const it = previewItems[selectedIndex]
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
      if(pid){
        // Tombstone locally — see markPreviewDeletedLocally/deletedPreviewIds.
        // The array only actually compacts the next time this item's
        // previews are freshly loaded (switching to another item and back,
        // or reopening the pane).
        markPreviewDeletedLocally(pid)
      } else {
        // No stable id to tombstone by (shouldn't normally happen — every
        // preview the server returns carries one) — fall back to a full
        // reload of this item's filmstrip, same as before.
        await loadPreviewsForItem(it)
      }
      notify('item-preview-updated', { id: it.id })
      alert('Preview deleted.')
    }catch(e){ console.error(e); alert('Failed to delete preview') }
    finally{ setDeleting(false) }
  }

  async function clearAllPreviews(){
    if(selectedIndex===null) return
    const it = previewItems[selectedIndex]
    if(!it) return
    const ok = window.confirm('Clear all previews for this item? This will remove all preview images.')
    if(!ok) return
    setDeleting(true)
    try{
      const resp = await fetch(`/api/items/${it.id}/previews/`, {method: 'DELETE'})
      if(!resp.ok){ const j = await resp.json().catch(()=>({})); alert('Failed to clear previews: '+(j.detail||j.error||resp.status)); return }
      setPreviews([])
      setCurrentPreviewIdx(0)
      // item-preview-updated below refreshes has_preview via App.jsx's own
      // listener, which will drop this item out of previewItems on its own
      // (it now has zero previews) — that naturally closes this modal via
      // the selectedIndex derivation above, no direct action needed here.
      notify('item-preview-updated', { id: it.id })
      alert('All previews cleared.')
    }catch(e){ console.error(e); alert('Failed to clear previews') }
    finally{ setDeleting(false) }
  }

  function prev(){
    if(selectedIndex===null || previewItems.length===0) return
    const it = previewItems[(selectedIndex - 1 + previewItems.length) % previewItems.length]
    if(it) setSelectedItemId(it.id)
  }

  function next(){
    if(selectedIndex===null || previewItems.length===0) return
    const it = previewItems[(selectedIndex + 1) % previewItems.length]
    if(it) setSelectedItemId(it.id)
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
          {previewItems.length===0 && (
            <div className="preview-empty">No previews available</div>
          )}
          <div className="preview-list">
            {previewItems.slice(panePageIndex*PANE_PAGE_SIZE, (panePageIndex+1)*PANE_PAGE_SIZE).map((it, localIdx) => {
              const globalIdx = panePageIndex * PANE_PAGE_SIZE + localIdx
              return (
                <div className="preview-item" key={it.id}>
                  <button className="preview-thumb-btn" onClick={()=>openLarge(globalIdx)}>
                    <img className="preview-thumb" src={`/api/items/${it.id}/preview/?index=0`} alt={it.title||''} loading="lazy" />
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
          {previewItems.length > PANE_PAGE_SIZE && (
            <div className="pane-pagination">
              <button className="btn" onClick={()=>setPanePageIndex(p=>Math.max(0,p-1))} disabled={panePageIndex===0}>Prev</button>
              <span>Page</span>
              <input
                type="number"
                min={1}
                max={Math.ceil(previewItems.length/PANE_PAGE_SIZE)}
                value={panePageIndex+1}
                onChange={e=>{
                  const v = parseInt(e.target.value,10)
                  if(!isNaN(v)) setPanePageIndex(Math.max(0, Math.min(Math.ceil(previewItems.length/PANE_PAGE_SIZE)-1, v-1)))
                }}
                style={{width:48, textAlign:'center'}}
              />
              <span>/ {Math.ceil(previewItems.length/PANE_PAGE_SIZE)}</span>
              <button className="btn" onClick={()=>setPanePageIndex(p=>Math.min(Math.ceil(previewItems.length/PANE_PAGE_SIZE)-1,p+1))} disabled={panePageIndex>=Math.ceil(previewItems.length/PANE_PAGE_SIZE)-1}>Next</button>
            </div>
          )}
        </div>
      </div>

      {selectedIndex!==null && previewItems[selectedIndex] && (
        <div className="preview-modal-backdrop" onClick={()=>setSelectedItemId(null)}>
          <div className="preview-modal">
              {/* Left/right full-height edge zones for consistent click areas */}
            <div className="modal-edge modal-edge-left" onClick={e=>{e.stopPropagation(); prev()}} aria-label="Previous" />
            <div className="modal-edge modal-edge-right" onClick={e=>{e.stopPropagation(); next()}} aria-label="Next" />

              {/* Close button (top-right) */}
              <button className="modal-close" onClick={()=>setSelectedItemId(null)} aria-label="Close">✕</button>

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
                const hasAnyPreview = (previews && previews.length>0)
                const aliveCount = (previews || []).filter(p => !deletedPreviewIds.has(p.id)).length
                const previewImgSrc = hasAnyPreview
                  ? `/api/items/${previewItems[selectedIndex].id}/preview/?index=${currentPreviewIdx}`
                  : `/api/items/${previewItems[selectedIndex].id}/preview/`
                // hasAnyPreview && aliveCount===0 means every one of this
                // item's images was just tombstoned in this same session —
                // currentPreviewIdx still points at a now-deleted entry
                // (nothing left to move onto, see markPreviewDeletedLocally),
                // so requesting it would just 404. Show a plain notice
                // instead until the next reload closes this modal for real
                // (the item itself will drop out of previewItems once
                // item-preview-updated's has_preview flip lands).
                const allDeleted = hasAnyPreview && aliveCount===0
                return (
              <div className="modal-top">
                <div className="modal-main">
                  {allDeleted ? (
                    <div className="preview-modal-all-deleted">この投稿の画像はすべて削除されました</div>
                  ) : (
                    <img className="preview-modal-img" src={previewImgSrc} alt={(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0])||previewItems[selectedIndex].title||''} />
                  )}
                </div>
                <div className="modal-meta">
                  <div className="preview-title">{(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0]) || previewItems[selectedIndex].titles || previewItems[selectedIndex].title || ''}</div>
                  <div className="preview-artist">{previewItems[selectedIndex].artist || ''}</div>
                  <a className="link-text" href={previewItems[selectedIndex].link} target="_blank" rel="noreferrer" style={{display:'inline-flex', alignItems:'center', gap:6}}>
                    {(() => {
                      const platform = getPlatformIcon(previewItems[selectedIndex].link)
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
                    // key is the preview's own stable DB id, not its
                    // position-based `index` -- deleting one image shifts
                    // every later one's index down (see item/views.py's
                    // preview_delete_by_id, which reindexes the remainder
                    // contiguously), so a positional key made React keep
                    // reusing DOM nodes by their OLD slot rather than
                    // tracking the actual image across the reindex.
                    //
                    // A deleted one renders as its own placeholder, in its
                    // OWN slot, instead of being removed from `previews`
                    // outright (see deletedPreviewIds/markPreviewDeletedLocally)
                    // -- every other thumbnail then stays exactly where it
                    // was, so there's never any ambiguity about which one
                    // was actually removed regardless of how many are on
                    // screen at once.
                    deletedPreviewIds.has(p.id) ? (
                      <div key={p.id} className="timeline-thumb-deleted" title="削除済み">
                        <span aria-hidden="true">🗑</span>
                        <span>削除済み</span>
                      </div>
                    ) : (
                      <img key={p.id} src={`/api/items/${previewItems[selectedIndex].id}/preview/?index=${p.index}`} alt={`preview-${p.index}`} className={currentPreviewIdx===p.index? 'timeline-thumb selected':'timeline-thumb'} onClick={()=>selectPreviewIndex(p.index)} />
                    )
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
