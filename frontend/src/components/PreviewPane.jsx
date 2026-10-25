import React, {useEffect, useState, useRef, useMemo} from 'react'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'
import EditFields from './EditFields'
import ImageTranslationOverlay from './ImageTranslationOverlay'

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
// `embedded`: renders as the normal in-flow "gallery" main view (App.jsx's
// viewMode==='gallery') instead of the fixed-position "Preview Timeline"
// overlay both panel chrome AND positioning differ, but it's the exact
// same component — same masonry grid, same lightbox — since both are
// fundamentally "browse every preview, click one for detail", just reached
// from a different place in the app.
export default function PreviewPane({open, onClose, readOnly, filteredItems, initialItemId, embedded=false, onLightboxClose}){
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
  const [, setForceUpdate] = useState(0)

  useEffect(()=>{
    function onPreviewItemUpdated(ev){
      const updated = ev && ev.detail && ev.detail.item
      if(!updated || updated.id == null) return
      const target = previewItems.find(it => it && (it.id === updated.id || String(it.id) === String(updated.id)))
      if(target){
        Object.assign(target, updated)
        setForceUpdate(n => n + 1)
      }
    }
    window.addEventListener('item-updated', onPreviewItemUpdated)
    return () => window.removeEventListener('item-updated', onPreviewItemUpdated)
  }, [previewItems])
  const [previews, setPreviews] = useState([]) // per-item preview list
  const [currentPreviewIdx, setCurrentPreviewIdx] = useState(0)
  const [selectedPreviewId, setSelectedPreviewId] = useState(null)
  const [editOpen, setEditOpen] = useState(false)
  const [translationsMap, setTranslationsMap] = useState({})
  const [translating, setTranslating] = useState(false)
  const [translationVisible, setTranslationVisible] = useState(true)
  const [translationNotice, setTranslationNotice] = useState('')
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
  // — doesn't apply when embedded: there's no floating pane to click "outside
  // of", it's just the page's own main content.
  useEffect(()=>{
    if(!open || embedded) return
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
  }, [open, onClose, embedded])

  useEffect(()=>{
    function onKey(e){
      if(selectedIndex===null) return
      // When edit modal is open, let user type/navigate within edit window without hotkeys moving background
      if(editOpen) return
      if(e.key==='Escape') closeLightbox()
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
  }, [selectedIndex, previewItems, previews, editOpen])

  // wheel navigation: accumulate deltas to avoid accidental small scrolls
  const wheelAccRef = useRef(0)
  const lastNavRef = useRef(0)

  useEffect(()=>{
    wheelAccRef.current = 0
  }, [editOpen])

  useEffect(()=>{
    function handleWheel(e){
      if(selectedIndex===null) return
      // When edit modal is open, scroll only inside the edit window — do not switch items in the background
      if(editOpen) return
      // If event originated from an overlay/form/dialog outside lightbox main area, do not trigger item nav
      if(e.target && e.target.closest && (e.target.closest('[role="dialog"]') || e.target.closest('form'))) return

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
  }, [selectedIndex, previewItems, editOpen])

  function openLarge(i){
    const it = previewItems[i]
    if(it) setSelectedItemId(it.id)
  }

  // A genuine "I'm done looking at this" close (Escape/backdrop/✕/item
  // deleted) — NOT the open-transition reset above, and not prev()/next()
  // moving between items. onLightboxClose lets App.jsx know specifically
  // so it can switch back out of Gallery mode when this lightbox was only
  // opened because of a List-view thumbnail click (see App.jsx's
  // openPreviewForItem) rather than the user actually choosing Gallery.
  function closeLightbox(){
    setSelectedItemId(null)
    if(onLightboxClose) onLightboxClose()
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
    setSelectMode(false)
    setPickedIds(new Set())
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
  const [deletingItem, setDeletingItem] = useState(false)
  // Multi-select over THIS item's own images, for deleting several at once or
  // for choosing the one(s) to keep ("keep" mode deletes everything else).
  const [selectMode, setSelectMode] = useState(false)
  const [pickedIds, setPickedIds] = useState(() => new Set())

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

  const currentItem = selectedIndex !== null ? previewItems[selectedIndex] : null
  const translationKey = currentItem ? `${currentItem.id}_${currentPreviewIdx}` : ''
  const currentTranslations = translationsMap[translationKey] || null

  // Check cached translations when current item or currentPreviewIdx changes
  useEffect(() => {
    setTranslationNotice('')
    if (!currentItem) return
    const key = `${currentItem.id}_${currentPreviewIdx}`
    if (translationsMap[key] !== undefined) return

    let active = true
    fetch(`/api/items/${currentItem.id}/translate-image/?index=${currentPreviewIdx}`, { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!active || !data) return
        if (data.cached && data.translations && data.translations.length > 0) {
          setTranslationsMap(prev => ({ ...prev, [key]: data.translations }))
        }
      })
      .catch(() => {})
    return () => { active = false }
  }, [currentItem?.id, currentPreviewIdx])

  async function handleTranslate(force = false) {
    if (!currentItem) return
    const key = `${currentItem.id}_${currentPreviewIdx}`
    if (!force && translationsMap[key] && translationsMap[key].length > 0) {
      setTranslationVisible(v => !v)
      return
    }

    setTranslating(true)
    setTranslationNotice('')
    try {
      const url = `/api/items/${currentItem.id}/translate-image/?index=${currentPreviewIdx}${force ? '&force=true' : ''}`
      const r = await fetch(url, { method: 'POST', credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) {
        if (j.code === 'NO_API_KEY') {
          throw new Error('Gemini APIキーが設定されていません。ヘッダーメニューの「画像翻訳 (Gemini) 設定」から設定してください。')
        }
        throw new Error(j.detail || `翻訳に失敗しました (${r.status})`)
      }
      setTranslationsMap(prev => ({ ...prev, [key]: j.translations || [] }))
      setTranslationVisible(true)
      const info = j.model ? ` [${j.model} / ${((j.elapsed_ms || 0) / 1000).toFixed(1)}秒]` : ''
      if (!j.translations || j.translations.length === 0) {
        setTranslationNotice(`外国語テキストは見つかりませんでした（日本語のみ、またはテキストなし）。${info}`)
      } else {
        setTranslationNotice(`${j.translations.length}件のテキストを翻訳しました。${info}`)
      }
    } catch (e) {
      setTranslationNotice(`エラー: ${e.message}`)
    } finally {
      setTranslating(false)
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

  function togglePicked(id){
    setPickedIds(prev => {
      const next = new Set(prev)
      if(next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function leaveSelectMode(){
    setSelectMode(false)
    setPickedIds(new Set())
  }

  // One call for both "delete these" and "keep only these" (see the backend's
  // delete_previews). The server renumbers the remaining images (and moves
  // their region labels with them), so unlike the single-image tombstone
  // above this reloads the filmstrip: every remaining thumbnail's index has
  // changed, and a stale index would show the wrong picture.
  async function runBulkDelete(body, confirmText){
    if(selectedIndex===null) return
    const it = previewItems[selectedIndex]
    if(!it) return
    if(!window.confirm(confirmText)) return
    setDeleting(true)
    try{
      const resp = await fetch(`/api/items/${it.id}/delete_previews/`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      })
      const j = await resp.json().catch(()=>({}))
      if(!resp.ok){ alert('Failed to delete previews: '+(j.detail||j.error||resp.status)); return }
      await loadPreviewsForItem(it)
      notify('item-preview-updated', { id: it.id })
      let msg = `${j.deleted}枚の画像を削除しました(残り${j.remaining}枚)。`
      if(j.regions_dropped) msg += `\n削除した画像上の領域指定 ${j.regions_dropped} 件も削除しました。`
      if(j.regions_remapped) msg += `\n残った画像の領域指定 ${j.regions_remapped} 件を新しい画像番号に付け替えました。`
      alert(msg)
    }catch(e){ console.error(e); alert('Failed to delete previews') }
    finally{ setDeleting(false) }
  }

  function aliveIds(){
    return (previews || []).filter(p => !deletedPreviewIds.has(p.id)).map(p => p.id)
  }

  function deletePicked(){
    const ids = [...pickedIds]
    if(ids.length === 0) return
    runBulkDelete({ delete_ids: ids },
      ids.length >= aliveIds().length
        ? `選んだ${ids.length}枚はこの投稿の画像すべてです。全部削除しますか？ 元に戻せません。`
        : `選んだ${ids.length}枚の画像を削除しますか？ 元に戻せません。`)
  }

  function keepPicked(){
    const ids = [...pickedIds]
    if(ids.length === 0) return
    const others = aliveIds().length - ids.length
    if(others <= 0){ alert('削除される画像がありません(すべて選択されています)'); return }
    runBulkDelete({ keep_ids: ids }, `選んだ${ids.length}枚だけを残し、それ以外の${others}枚を削除しますか？ 元に戻せません。`)
  }

  function keepOnlyCurrent(){
    const pid = selectedPreviewId || ((previews && previews[currentPreviewIdx]) ? previews[currentPreviewIdx].id : null)
    if(!pid) return
    const others = aliveIds().length - 1
    if(others <= 0){ alert('この投稿の画像は1枚だけです'); return }
    runBulkDelete({ keep_ids: [pid] }, `いま表示している画像だけを残し、それ以外の${others}枚を削除しますか？ 元に戻せません。`)
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

  // Mirrors ScrollList.jsx's ItemRow "Delete item" button exactly (same
  // endpoint, same confirm wording) — the gallery's lightbox is meant to
  // cover the same curate-or-discard decisions the card list's own action
  // row does, not a reduced read-only view.
  async function deleteItem(){
    if(selectedIndex===null) return
    const it = previewItems[selectedIndex]
    if(!it) return
    const ok = window.confirm('Delete this item from the database? This will remove its previews too.')
    if(!ok) return
    setDeletingItem(true)
    try{
      const resp = await fetch(`/api/items/${it.id}/delete_item/`, {method:'DELETE'})
      if(!resp.ok){ const j = await resp.json().catch(()=>({})); alert('Failed to delete item: '+(j.detail||j.error||resp.status)); return }
      notify('item-deleted', { id: it.id })
      closeLightbox()
    }catch(e){ console.error(e); alert('Failed to delete item') }
    finally{ setDeletingItem(false) }
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
      <div className={embedded ? 'preview-pane preview-pane-embedded' : 'preview-pane'} ref={previewPaneRef}>
        {!embedded && (
          <div className="preview-header">
            <strong>Preview Timeline</strong>
            <div className="preview-controls">
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
        <div className="preview-body">
          {previewItems.length===0 && (
            <div className="preview-empty">No previews available</div>
          )}
          {/* Image-only grid — no title/artist text on the cells themselves
              (see .preview-grid's own comment in styles.css): the ask was
              for a Pixiv-feed-style browse where nothing but the images
              are visible until you actually click one. */}
          <div className="preview-grid">
            {previewItems.slice(panePageIndex*PANE_PAGE_SIZE, (panePageIndex+1)*PANE_PAGE_SIZE).map((it, localIdx) => {
              const globalIdx = panePageIndex * PANE_PAGE_SIZE + localIdx
              return (
                <button className="preview-grid-cell" key={it.id} onClick={()=>openLarge(globalIdx)}>
                  <img src={`/api/items/${it.id}/preview/?index=0`} alt={it.title||''} loading="lazy" />
                </button>
              )
            })}
          </div>
          {/* App.jsx already paginates `filteredItems` down to one page's
              worth before handing it to an embedded PreviewPane (see its own
              call site comment) — this pane's own paging would otherwise be
              a second, out-of-sync page number sitting right above App.jsx's
              own Pagination component for the exact same list. */}
          {!embedded && previewItems.length > PANE_PAGE_SIZE && (
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
        <div className="preview-modal-backdrop" onClick={closeLightbox}>
          <div className="preview-modal">
              {/* Left/right full-height edge zones for consistent click areas */}
            <div className="modal-edge modal-edge-left" onClick={e=>{e.stopPropagation(); prev()}} aria-label="Previous" />
            <div className="modal-edge modal-edge-right" onClick={e=>{e.stopPropagation(); next()}} aria-label="Next" />

              {/* Close button (top-right) */}
              <button className="modal-close" onClick={closeLightbox} aria-label="Close">✕</button>

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
                    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', maxWidth: '70vw', maxHeight: '78vh' }}>
                      <img className="preview-modal-img" src={previewImgSrc} alt={(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0])||previewItems[selectedIndex].title||''} />
                      <ImageTranslationOverlay translations={currentTranslations} visible={translationVisible} />
                    </div>
                  )}
                </div>
                <div className="modal-meta">
                  <div style={{display:'flex', alignItems:'center', gap:8, flexWrap:'wrap', marginBottom:4}}>
                    <div className="preview-title">{(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0]) || previewItems[selectedIndex].titles || previewItems[selectedIndex].title || ''}</div>
                    {previewItems[selectedIndex].situation && (
                      <span className={`situation-chip s-${previewItems[selectedIndex].situation.toLowerCase()}`}>
                        {previewItems[selectedIndex].situation.toUpperCase()}
                      </span>
                    )}
                  </div>
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
                  <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <button
                        className="btn"
                        onClick={() => handleTranslate(false)}
                        disabled={translating}
                        style={{
                          background: (currentTranslations && currentTranslations.length > 0)
                            ? (translationVisible ? '#2563eb' : '#334155')
                            : '#1e293b',
                          border: '1px solid #3b82f6',
                          color: '#fff',
                          padding: '6px 12px',
                          fontSize: 12,
                          fontWeight: 600,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                        }}
                        title="画像内の外国語テキスト（英語・韓国語・中国語）を検出して日本語訳を表示します"
                      >
                        <span style={{ fontSize: 14 }}>🌐</span>
                        <span>
                          {translating
                            ? '翻訳中…'
                            : (currentTranslations !== null)
                            ? (currentTranslations.length > 0
                              ? `翻訳: ${translationVisible ? '表示中' : '非表示'} (${currentTranslations.length}件)`
                              : '翻訳: テキストなし')
                            : '画像内の外国語を翻訳'}
                        </span>
                      </button>
                      {currentTranslations && !translating && (
                        <button
                          className="btn"
                          onClick={() => handleTranslate(true)}
                          style={{ padding: '6px 8px', fontSize: 11, background: '#1e293b', border: '1px solid #475569', color: '#cbd5e1' }}
                          title="キャッシュを無視してGeminiで再翻訳"
                        >
                          ↻ 再翻訳
                        </button>
                      )}
                    </div>
                    {translationNotice && (
                      <div style={{ fontSize: 11, color: '#94a3b8', lineHeight: 1.4 }}>
                        {translationNotice}
                      </div>
                    )}
                  </div>
                  {!readOnly && (
                    <div style={{marginTop:12}}>
                      {/* Same "sweep" glyph ScrollList.jsx's own Clear-previews button uses
                          (not the trash-can — that's reserved for whole-item delete, see
                          Delete item there) so both places that mean "clear preview
                          image(s), not the item itself" read as the same action. */}
                      <button className="btn" style={{padding:'7px 10px', lineHeight:1, position:'relative'}} title="Delete this preview (only this one image)" onClick={deleteCurrentPreview} disabled={deleting}>
                        {deleting ? '…' : (
                          <>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M4 6h16"/><path d="M4 6c0 2 1 3 3 3"/><path d="M20 6c0 2-1 3-3 3"/><line x1="12" y1="6" x2="12" y2="20"/></svg>
                            {/* "1" badge distinguishes this from "Clear all previews" below */}
                            <span style={{position:'absolute', top:-4, right:-4, background:'#3b82f6', color:'#fff',
                              borderRadius:'50%', width:14, height:14, fontSize:10, lineHeight:'14px',
                              textAlign:'center', fontWeight:700}}>1</span>
                          </>
                        )}
                      </button>
                      <button className="btn" style={{marginLeft:8, background:'#ea580c', color:'#fff', padding:'7px 10px', lineHeight:1}} title="Clear all previews" onClick={clearAllPreviews} disabled={deleting}>
                        {deleting ? '…' : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M4 6h16"/><path d="M4 6c0 2 1 3 3 3"/><path d="M20 6c0 2-1 3-3 3"/><line x1="12" y1="6" x2="12" y2="20"/></svg>}
                      </button>
                      {/* Editing/whole-item delete were missing here before —
                          this lightbox used to only manage preview IMAGES,
                          so curating (retagging, or discarding a bad item
                          entirely) meant leaving it for the List view's own
                          card. Mirrors ScrollList's exact buttons/icons. */}
                      <button className="btn" style={{marginLeft:8, padding:'7px 10px', lineHeight:1}} title="Edit fields" onClick={()=>setEditOpen(true)}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                      </button>
                      <button className="btn" style={{marginLeft:8, background:'#a33', color:'#fff', padding:'7px 10px', lineHeight:1}} title="Delete item" onClick={deleteItem} disabled={deletingItem}>
                        {deletingItem ? '…' : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>}
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
                {!readOnly && previews && previews.filter(p => !deletedPreviewIds.has(p.id)).length > 1 && (
                  <div style={{display:'flex', gap:6, flexWrap:'wrap', alignItems:'center', marginBottom:6, fontSize:12}}>
                    {!selectMode ? (
                      <>
                        <button className="btn" style={{fontSize:12}} onClick={()=>{ setSelectMode(true); setPickedIds(new Set()) }}
                          title="複数の画像を選んで、まとめて削除する/選んだ画像だけ残す">☑ 複数選択</button>
                        <button className="btn" style={{fontSize:12}} onClick={keepOnlyCurrent} disabled={deleting}
                          title="いま大きく表示している画像だけを残して、他の画像をすべて削除します">★ この1枚だけ残す</button>
                      </>
                    ) : (
                      <>
                        <span style={{color:'#cbd5e1'}}>{pickedIds.size}枚選択中(画像をクリックで選択)</span>
                        <button className="btn" style={{fontSize:12}} onClick={()=>setPickedIds(new Set(aliveIds()))}>すべて選択</button>
                        <button className="btn" style={{fontSize:12}} onClick={()=>setPickedIds(new Set())}>選択解除</button>
                        <button className="btn" style={{fontSize:12, background:'#a33', color:'#fff'}} disabled={deleting || pickedIds.size===0} onClick={deletePicked}
                          title="選んだ画像を削除します">選んだ画像を削除</button>
                        <button className="btn" style={{fontSize:12, background:'#166534', color:'#fff'}} disabled={deleting || pickedIds.size===0} onClick={keepPicked}
                          title="選んだ画像だけを残し、それ以外を削除します">選んだ画像だけ残す</button>
                        <button className="btn" style={{fontSize:12}} onClick={leaveSelectMode}>キャンセル</button>
                      </>
                    )}
                  </div>
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
                      <div key={p.id} style={{position:'relative', flex:'0 0 auto', lineHeight:0}}>
                        <img src={`/api/items/${previewItems[selectedIndex].id}/preview/?index=${p.index}`} alt={`preview-${p.index}`}
                          className={currentPreviewIdx===p.index? 'timeline-thumb selected':'timeline-thumb'}
                          style={selectMode && pickedIds.has(p.id) ? {borderColor:'#3b82f6', opacity:1} : undefined}
                          onClick={()=> selectMode ? togglePicked(p.id) : selectPreviewIndex(p.index)} />
                        {selectMode && (
                          <span aria-hidden="true" style={{position:'absolute', top:4, left:4, width:18, height:18, borderRadius:4,
                            background: pickedIds.has(p.id) ? '#2563eb' : 'rgba(0,0,0,0.55)', border:'1px solid #fff', color:'#fff',
                            fontSize:12, lineHeight:'16px', textAlign:'center', pointerEvents:'none'}}>{pickedIds.has(p.id) ? '✓' : ''}</span>
                        )}
                      </div>
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

      {editOpen && selectedIndex!==null && previewItems[selectedIndex] && (
        <EditFields
          item={previewItems[selectedIndex]}
          onClose={()=>setEditOpen(false)}
          onSaved={(newItem)=>{
            setEditOpen(false)
            Object.assign(previewItems[selectedIndex], newItem)
            setForceUpdate(n => n + 1)
            notify('item-updated', { id: previewItems[selectedIndex].id, item: newItem })
          }}
        />
      )}
    </>
  )
}
