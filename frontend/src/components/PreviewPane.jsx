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
  const [reordering, setReordering] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [translationsMap, setTranslationsMap] = useState({})
  const [translating, setTranslating] = useState(false)
  // Translation display mode: 'always' (常時表示) | 'hover' (カーソル時のみ表示) | 'hidden' (非表示)
  const [translationMode, setTranslationMode] = useState(() => {
    try {
      return localStorage.getItem('fv_translation_mode') || 'always'
    } catch {
      return 'always'
    }
  })

  function changeTranslationMode(newMode) {
    setTranslationMode(newMode)
    try {
      localStorage.setItem('fv_translation_mode', newMode)
    } catch {}
  }

  // Reader layout mode: 'comic' (縦スクロール漫画ビュー) | 'single' (1枚ずつ表示)
  const [readerLayout, setReaderLayout] = useState(() => {
    try {
      return localStorage.getItem('fv_reader_layout') || 'comic'
    } catch {
      return 'comic'
    }
  })

  function changeReaderLayout(mode) {
    setReaderLayout(mode)
    try {
      localStorage.setItem('fv_reader_layout', mode)
    } catch {}
  }

  const comicContainerRef = useRef(null)

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

      if(readerLayout === 'comic'){
        // In comic vertical reader, Left/Right navigate between items
        if(e.key==='ArrowLeft'){ e.preventDefault(); prev(); if(comicContainerRef.current) comicContainerRef.current.scrollTop = 0; return }
        if(e.key==='ArrowRight'){ e.preventDefault(); next(); if(comicContainerRef.current) comicContainerRef.current.scrollTop = 0; return }

        // Keyboard scrolling support for comic mode
        if(e.key==='ArrowDown' || e.key==='PageDown' || e.key===' '){
          if(comicContainerRef.current){
            e.preventDefault()
            const delta = e.key==='ArrowDown' ? 140 : Math.round(comicContainerRef.current.clientHeight * 0.8)
            comicContainerRef.current.scrollTop += delta
          }
          return
        }
        if(e.key==='ArrowUp' || e.key==='PageUp'){
          if(comicContainerRef.current){
            e.preventDefault()
            const delta = e.key==='ArrowUp' ? 140 : Math.round(comicContainerRef.current.clientHeight * 0.8)
            comicContainerRef.current.scrollTop -= delta
          }
          return
        }
        if(e.key==='Home'){
          if(comicContainerRef.current){ e.preventDefault(); comicContainerRef.current.scrollTop = 0 }
          return
        }
        if(e.key==='End'){
          if(comicContainerRef.current){ e.preventDefault(); comicContainerRef.current.scrollTop = comicContainerRef.current.scrollHeight }
          return
        }
      } else {
        // Single page mode: Up/Down move to prev/next item, Left/Right page through current item's images
        if(e.key==='ArrowUp') prev()
        if(e.key==='ArrowDown') next()
        if(e.key==='ArrowLeft'){ e.preventDefault(); prevPreviewImage() }
        if(e.key==='ArrowRight'){ e.preventDefault(); nextPreviewImage() }
      }
    }
    window.addEventListener('keydown', onKey)
    return ()=> window.removeEventListener('keydown', onKey)
  }, [selectedIndex, previewItems, previews, editOpen, readerLayout])

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

      // In comic vertical reader mode, mouse wheel anywhere in modal scrolls the comic smoothly!
      if(readerLayout === 'comic'){
        if(comicContainerRef.current){
          const delta = e.deltaY || e.deltaX || 0
          if(delta !== 0){
            comicContainerRef.current.scrollTop += delta
          }
        }
        return
      }

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
  }, [selectedIndex, previewItems, editOpen, readerLayout])

  const [showComicTimeline, setShowComicTimeline] = useState(false)

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
    if(comicContainerRef.current) comicContainerRef.current.scrollTop = 0
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
        if (data.cached && data.translations && data.translations.length > 0
            && (data.image_index == null || data.image_index === currentPreviewIdx)) {
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
      // Cycle modes on main button click: always -> hover -> hidden -> always
      const nextMode = translationMode === 'always' ? 'hover' : (translationMode === 'hover' ? 'hidden' : 'always')
      changeTranslationMode(nextMode)
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
      // Store under the image the server actually translated (never the
      // first page by mistake), even if the user paged away meanwhile.
      const resultIdx = (j.image_index != null) ? j.image_index : currentPreviewIdx
      const resultKey = `${currentItem.id}_${resultIdx}`
      setTranslationsMap(prev => ({ ...prev, [resultKey]: j.translations || [] }))
      if (resultKey !== key) return
      if (translationMode === 'hidden') {
        changeTranslationMode('always')
      }
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

  // Chapter / episode checkpoints for multi-page manga:
  // e.g. [{ page_index: 0, label: "第1話" }, { page_index: 24, label: "第2話" }]
  const [checkpoints, setCheckpoints] = useState([])
  const [activeChapterIndex, setActiveChapterIndex] = useState(0)

  useEffect(() => {
    setCheckpoints(Array.isArray(currentItem?.checkpoints) ? currentItem.checkpoints : [])
  }, [currentItem?.id, currentItem?.checkpoints])

  const checkpointsMap = useMemo(() => {
    const map = new Map()
    for (const cp of checkpoints) {
      if (cp && typeof cp.page_index === 'number') {
        map.set(cp.page_index, cp)
      }
    }
    return map
  }, [checkpoints])

  // Track active chapter / checkpoint as user scrolls comic container
  useEffect(() => {
    if (readerLayout !== 'comic' || !comicContainerRef.current || checkpoints.length === 0) {
      return
    }
    const container = comicContainerRef.current
    function onScroll() {
      const containerTop = container.scrollTop
      let cur = 0
      for (let i = 0; i < checkpoints.length; i++) {
        const el = document.getElementById(`comic-page-${checkpoints[i].page_index}`)
        if (el && (el.offsetTop - 140) <= containerTop) {
          cur = i
        }
      }
      setActiveChapterIndex(cur)
    }
    container.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => container.removeEventListener('scroll', onScroll)
  }, [readerLayout, checkpoints, previews])

  async function saveCheckpoints(newCps) {
    if (!currentItem) return
    const sorted = [...newCps].sort((a, b) => a.page_index - b.page_index)
    setCheckpoints(sorted)
    try {
      const res = await fetch(`/api/items/${currentItem.id}/checkpoints/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ checkpoints: sorted }),
        credentials: 'same-origin',
      })
      if (res.ok) {
        const data = await res.json()
        if (data.item) {
          Object.assign(currentItem, data.item)
          window.dispatchEvent(new CustomEvent('item-updated', { detail: { item: data.item } }))
        }
      }
    } catch (err) {
      console.error('Failed to save checkpoints:', err)
    }
  }

  function handleAddCheckpoint(pageIndex, initialLabel) {
    const existing = checkpoints.find(c => c.page_index === pageIndex)
    const label = window.prompt(
      `ページ ${pageIndex + 1} のチェックポイント名（話数・章タイトル）を入力してください:`,
      existing ? existing.label : (initialLabel || `第${checkpoints.length + 1}話`)
    )
    if (label === null) return
    const trimmed = label.trim() || `第${checkpoints.length + 1}話`
    const filtered = checkpoints.filter(c => c.page_index !== pageIndex)
    saveCheckpoints([...filtered, { page_index: pageIndex, label: trimmed }])
  }

  function handleEditCheckpoint(pageIndex) {
    const existing = checkpoints.find(c => c.page_index === pageIndex)
    if (!existing) return
    const label = window.prompt(`チェックポイント名を変更:`, existing.label)
    if (label === null) return
    const trimmed = label.trim() || existing.label
    saveCheckpoints(checkpoints.map(c => c.page_index === pageIndex ? { ...c, label: trimmed } : c))
  }

  function handleRemoveCheckpoint(pageIndex) {
    const existing = checkpoints.find(c => c.page_index === pageIndex)
    if (!existing) return
    if (!window.confirm(`チェックポイント「${existing.label}」(P.${pageIndex + 1}) を削除しますか？`)) return
    saveCheckpoints(checkpoints.filter(c => c.page_index !== pageIndex))
  }

  function jumpToPage(pageIndex) {
    selectPreviewIndex(pageIndex)
    if (readerLayout === 'comic') {
      const el = document.getElementById(`comic-page-${pageIndex}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
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

  async function runReorder(body) {
    if (selectedIndex === null) return
    const it = previewItems[selectedIndex]
    if (!it || reordering) return
    setReordering(true)
    try {
      const resp = await fetch(`/api/items/${it.id}/reorder_previews/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        alert('画像の並び替えに失敗しました: ' + (j.detail || j.error || resp.status))
        return
      }
      if (j.previews) {
        setPreviews(j.previews)
        const curPid = selectedPreviewId || (previews[currentPreviewIdx] && previews[currentPreviewIdx].id)
        if (curPid) {
          const newPos = j.previews.findIndex(p => p.id === curPid)
          if (newPos >= 0) setCurrentPreviewIdx(newPos)
        }
      }
      notify('item-preview-updated', { id: it.id })
    } catch (e) {
      console.error(e)
      alert('画像の並び替えに失敗しました')
    } finally {
      setReordering(false)
    }
  }

  function reorderByFilename() {
    runReorder({ action: 'sort_by_filename' })
  }

  function reverseOrder() {
    runReorder({ action: 'reverse' })
  }

  function moveCurrentImage(direction) {
    const curPid = selectedPreviewId || (previews[currentPreviewIdx] && previews[currentPreviewIdx].id)
    if (!curPid) return
    runReorder({ action: 'move', preview_id: curPid, direction })
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
            {/* Left/right edge click zones only active in single-page mode so they don't block vertical scrolling */}
            {readerLayout === 'single' && (
              <>
                <div className="modal-edge modal-edge-left" onClick={e=>{e.stopPropagation(); prev()}} aria-label="Previous" />
                <div className="modal-edge modal-edge-right" onClick={e=>{e.stopPropagation(); next()}} aria-label="Next" />
              </>
            )}

            {/* Close button (top-right) */}
            <button className="modal-close" onClick={closeLightbox}>✕</button>

            <div className={`modal-content ${readerLayout === 'comic' ? 'comic-mode' : ''}`} onClick={e=>e.stopPropagation()}>
              {(() => {
                const hasAnyPreview = (previews && previews.length>0)
                const alivePreviews = (previews || []).filter(p => !deletedPreviewIds.has(p.id))
                const aliveCount = alivePreviews.length
                const previewImgSrc = hasAnyPreview
                  ? `/api/items/${previewItems[selectedIndex].id}/preview/?index=${currentPreviewIdx}`
                  : `/api/items/${previewItems[selectedIndex].id}/preview/`
                const allDeleted = hasAnyPreview && aliveCount===0
                return (
              <div className={`modal-top ${readerLayout === 'comic' ? 'comic-mode' : ''}`}>
                <div className={`modal-main ${readerLayout === 'comic' ? 'comic-mode' : ''}`}>
                  {allDeleted ? (
                    <div className="preview-modal-all-deleted">この投稿の画像はすべて削除されました</div>
                  ) : readerLayout === 'comic' && aliveCount > 0 ? (
                    <div
                      ref={comicContainerRef}
                      className="comic-scroll-container"
                      tabIndex={0}
                    >
                      {previews.map(p => {
                        if (deletedPreviewIds.has(p.id)) return null
                        return (
                          <div
                            key={p.id}
                            id={`comic-page-${p.index}`}
                            className="comic-page-wrap"
                          >
                            {checkpointsMap.has(p.index) && (
                              <div className="comic-chapter-banner">
                                <div className="comic-chapter-banner-label">
                                  <span>🚩</span>
                                  <span style={{ fontWeight: 700 }}>{checkpointsMap.get(p.index).label}</span>
                                  <span className="comic-chapter-banner-page">P.{p.index + 1}</span>
                                </div>
                                <div className="comic-chapter-banner-actions">
                                  <button
                                    type="button"
                                    className="comic-cp-btn-sm"
                                    onClick={(e) => { e.stopPropagation(); handleEditCheckpoint(p.index) }}
                                    title="チェックポイント名を変更"
                                  >
                                    ✏️ 編集
                                  </button>
                                  <button
                                    type="button"
                                    className="comic-cp-btn-sm danger"
                                    onClick={(e) => { e.stopPropagation(); handleRemoveCheckpoint(p.index) }}
                                    title="チェックポイントを削除"
                                  >
                                    ✕ 削除
                                  </button>
                                </div>
                              </div>
                            )}
                            <div className="comic-page-header">
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span>ページ {p.index + 1} / {aliveCount}</span>
                                {checkpointsMap.has(p.index) ? (
                                  <span className="comic-cp-badge" title="話数チェックポイント">
                                    🚩 {checkpointsMap.get(p.index).label}
                                  </span>
                                ) : (
                                  <button
                                    type="button"
                                    className="comic-add-cp-link"
                                    onClick={(e) => { e.stopPropagation(); handleAddCheckpoint(p.index) }}
                                    title="このページを新しい話数/チェックポイントとして登録"
                                  >
                                    ＋ 🚩 チェックポイント追加
                                  </button>
                                )}
                              </div>
                              <span style={{ opacity: 0.7 }}>{p.filename || ''}</span>
                            </div>
                            <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
                              <img
                                className="comic-page-img"
                                src={`/api/items/${previewItems[selectedIndex].id}/preview/?index=${p.index}`}
                                alt={`Page ${p.index + 1}`}
                                loading={p.index < 2 ? 'eager' : 'lazy'}
                                decoding="async"
                              />
                              <ImageTranslationOverlay
                                translations={translationsMap[`${previewItems[selectedIndex].id}_${p.index}`]}
                                mode={translationMode}
                              />
                            </div>
                          </div>
                        )
                      })}
                      <div className="comic-finish-footer">
                        <div style={{ fontSize: 16, fontWeight: 700, color: '#f8fafc', marginBottom: 6 }}>
                          📖 この話を読み終わりました (全 {aliveCount} ページ)
                        </div>
                        <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 16 }}>
                          {(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0]) || previewItems[selectedIndex].title || ''}
                        </div>
                        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', alignItems: 'center', flexWrap: 'wrap' }}>
                          <button
                            className="btn"
                            onClick={() => { prev(); if(comicContainerRef.current) comicContainerRef.current.scrollTop = 0 }}
                            disabled={selectedIndex === 0}
                            style={{ padding: '8px 16px', fontSize: 13 }}
                          >
                            ◀ 前の作品へ
                          </button>
                          <button
                            className="btn"
                            onClick={() => { if(comicContainerRef.current) comicContainerRef.current.scrollTo({ top: 0, behavior: 'smooth' }) }}
                            style={{ padding: '8px 14px', fontSize: 13 }}
                          >
                            ⬆ 最初に戻る
                          </button>
                          <button
                            className="btn"
                            onClick={() => { next(); if(comicContainerRef.current) comicContainerRef.current.scrollTop = 0 }}
                            disabled={selectedIndex >= previewItems.length - 1}
                            style={{ padding: '8px 20px', fontSize: 13, background: '#2563eb', color: '#fff', fontWeight: 600 }}
                          >
                            次の作品へ ▶
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', maxWidth: '100%', maxHeight: '100%' }}>
                      <img className="preview-modal-img" src={previewImgSrc} alt={(previewItems[selectedIndex].titles && previewItems[selectedIndex].titles[0])||previewItems[selectedIndex].title||''} />
                      <ImageTranslationOverlay translations={currentTranslations} mode={translationMode} />
                    </div>
                  )}
                </div>
                <div className={`modal-meta ${readerLayout === 'comic' ? 'comic-mode' : ''}`}>
                  {/* View layout mode switch */}
                  <div style={{ display: 'flex', gap: 4, background: 'rgba(15, 23, 42, 0.6)', padding: 3, borderRadius: 6, border: '1px solid rgba(255,255,255,0.1)', marginBottom: 8 }}>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => changeReaderLayout('comic')}
                      style={{
                        flex: 1,
                        fontSize: 11,
                        padding: '6px 4px',
                        background: readerLayout === 'comic' ? '#2563eb' : 'transparent',
                        color: readerLayout === 'comic' ? '#fff' : '#94a3b8',
                        border: 'none',
                        borderRadius: 4,
                        fontWeight: readerLayout === 'comic' ? 600 : 400,
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 4,
                        whiteSpace: 'nowrap'
                      }}
                      title="漫画ビュー: 縦スクロールで1ページ内に全画像をおさめ、スクロールで一気に読み進めます"
                    >
                      <span>📖</span> 漫画 (スクロール)
                    </button>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => changeReaderLayout('single')}
                      style={{
                        flex: 1,
                        fontSize: 11,
                        padding: '6px 4px',
                        background: readerLayout === 'single' ? '#2563eb' : 'transparent',
                        color: readerLayout === 'single' ? '#fff' : '#94a3b8',
                        border: 'none',
                        borderRadius: 4,
                        fontWeight: readerLayout === 'single' ? 600 : 400,
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 4,
                        whiteSpace: 'nowrap'
                      }}
                      title="単一ページビュー: 1画像ずつ大きく切り替えて表示します"
                    >
                      <span>🖼</span> 単一ページ
                    </button>
                  </div>

                  {readerLayout === 'comic' && previews && previews.length > 1 && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => setShowComicTimeline(v => !v)}
                      style={{
                        fontSize: 11,
                        padding: '4px 8px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 5,
                        background: showComicTimeline ? '#334155' : 'rgba(15, 23, 42, 0.5)',
                        border: '1px solid rgba(255,255,255,0.15)',
                        marginBottom: 4
                      }}
                      title="下部のサムネイル一覧（目次）の表示/非表示を切り替えます"
                    >
                      <span>📑</span>
                      <span>{showComicTimeline ? 'ページ一覧 (サムネイル) を隠す ▲' : 'ページ一覧 (サムネイル) を表示 ▼'}</span>
                    </button>
                  )}

                  {previews && previews.length > 1 && (
                    <div className="comic-chapters-panel">
                      <div className="comic-chapters-header">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                          <span>📑</span>
                          <span>話数・目次</span>
                          {checkpoints.length > 0 && (
                            <span style={{ fontSize: 10, background: '#2563eb', color: '#fff', padding: '1px 5px', borderRadius: 10, fontWeight: 700 }}>
                              {checkpoints.length}話
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 11, padding: '2px 8px', background: 'rgba(59, 130, 246, 0.2)', border: '1px solid #3b82f6', color: '#93c5fd', borderRadius: 4 }}
                          onClick={() => {
                            const defaultPg = Math.min(aliveCount, (currentPreviewIdx + 1) || 1)
                            const pStr = window.prompt(`チェックポイントを追加するページ番号を入力 (1〜${aliveCount}):`, defaultPg)
                            if (pStr === null) return
                            const pNum = parseInt(pStr, 10)
                            if (isNaN(pNum) || pNum < 1 || pNum > aliveCount) {
                              alert(`1〜${aliveCount} の範囲で指定してください。`)
                              return
                            }
                            handleAddCheckpoint(pNum - 1)
                          }}
                          title="指定ページに話数チェックポイントを追加"
                        >
                          ＋ 🚩 追加
                        </button>
                      </div>
                      <div className="comic-chapters-list">
                        {checkpoints.length === 0 ? (
                          <div className="comic-chapters-empty">
                            チェックポイント（話数の区切り）は未設定です。<br />
                            「＋ 🚩 追加」または各ページヘッダーから追加すると、1クリックでその話へジャンプできます。
                          </div>
                        ) : (
                          checkpoints.map((cp, idx) => {
                            const isActive = activeChapterIndex === idx
                            return (
                              <div
                                key={cp.page_index}
                                className={`comic-chapter-item ${isActive ? 'active' : ''}`}
                                onClick={() => jumpToPage(cp.page_index)}
                                title={`クリックして ${cp.label} (P.${cp.page_index + 1}) へジャンプ`}
                              >
                                <span style={{ marginRight: 6, opacity: 0.85, fontSize: 11 }}>🚩</span>
                                <span className="comic-chapter-item-label">{cp.label}</span>
                                <span className="comic-chapter-item-page">P.{cp.page_index + 1}</span>
                                <div className="comic-chapter-item-tools" onClick={e => e.stopPropagation()}>
                                  <button
                                    type="button"
                                    className="comic-cp-btn-sm"
                                    onClick={() => handleEditCheckpoint(cp.page_index)}
                                    title="名前を変更"
                                  >
                                    ✏️
                                  </button>
                                  <button
                                    type="button"
                                    className="comic-cp-btn-sm danger"
                                    onClick={() => handleRemoveCheckpoint(cp.page_index)}
                                    title="削除"
                                  >
                                    ✕
                                  </button>
                                </div>
                              </div>
                            )
                          })
                        )}
                      </div>
                    </div>
                  )}

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
                            ? (translationMode !== 'hidden' ? '#2563eb' : '#334155')
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
                        title="画像内の外国語テキスト（英語・韓国語・中国語）を検出して日本語訳を表示します。クリックで表示モード切り替え（常時→カーソル時→非表示）"
                      >
                        <span style={{ fontSize: 14 }}>🌐</span>
                        <span>
                          {translating
                            ? '翻訳中…'
                            : (currentTranslations !== null)
                            ? (currentTranslations.length > 0
                              ? `翻訳: ${translationMode === 'always' ? '常時表示' : (translationMode === 'hover' ? 'カーソル時表示' : '非表示')} (${currentTranslations.length}件)`
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
                    {currentTranslations && currentTranslations.length > 0 && (
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 2, background: 'rgba(15, 23, 42, 0.75)', padding: '2px 4px', borderRadius: 6, border: '1px solid rgba(59, 130, 246, 0.3)', width: 'fit-content' }}>
                        <span style={{ fontSize: 11, color: '#94a3b8', padding: '0 4px' }}>表示:</span>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => changeTranslationMode('always')}
                          style={{
                            fontSize: 11,
                            padding: '2px 7px',
                            background: translationMode === 'always' ? '#2563eb' : 'transparent',
                            color: translationMode === 'always' ? '#fff' : '#94a3b8',
                            border: 'none',
                            borderRadius: 4,
                            fontWeight: translationMode === 'always' ? 600 : 400,
                          }}
                          title="すべての吹き出しを常時表示します"
                        >
                          👁 常時
                        </button>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => changeTranslationMode('hover')}
                          style={{
                            fontSize: 11,
                            padding: '2px 7px',
                            background: translationMode === 'hover' ? '#2563eb' : 'transparent',
                            color: translationMode === 'hover' ? '#fff' : '#94a3b8',
                            border: 'none',
                            borderRadius: 4,
                            fontWeight: translationMode === 'hover' ? 600 : 400,
                          }}
                          title="該当のセリフ部分にカーソルを合わせたときのみ翻訳を表示します"
                        >
                          👆 カーソル時
                        </button>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => changeTranslationMode('hidden')}
                          style={{
                            fontSize: 11,
                            padding: '2px 7px',
                            background: translationMode === 'hidden' ? '#475569' : 'transparent',
                            color: translationMode === 'hidden' ? '#fff' : '#94a3b8',
                            border: 'none',
                            borderRadius: 4,
                            fontWeight: translationMode === 'hidden' ? 600 : 400,
                          }}
                          title="翻訳を非表示にします"
                        >
                          ✕ 非表示
                        </button>
                      </div>
                    )}
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
              {(readerLayout !== 'comic' || showComicTimeline) && (
                <div className="modal-timeline-wrap">
                  {previews && previews.length>1 && (
                    <div className="modal-timeline-hint">
                      {readerLayout === 'comic'
                        ? '縦スクロールで通し読み / サムネイルクリックで各ページへ移動 / ←・→キーで前後の作品へ'
                        : '←/→キーでこのアイテムの前後のページへ / ↑/↓キーで前後の作品へ'}
                    </div>
                  )}
                  {!readOnly && previews && previews.filter(p => !deletedPreviewIds.has(p.id)).length > 1 && (
                    <div style={{display:'flex', gap:6, flexWrap:'wrap', alignItems:'center', marginBottom:6, fontSize:12}}>
                      {!selectMode ? (
                        <>
                          <button className="btn" style={{fontSize:12}} onClick={()=>{ setSelectMode(true); setPickedIds(new Set()) }}
                            title="複数の画像を選んで、まとめて削除する/選んだ画像だけ残す">☑ 複数選択</button>
                          <button className="btn" style={{fontSize:12}} onClick={keepOnlyCurrent} disabled={deleting || reordering}
                            title="いま大きく表示している画像だけを残して、他の画像をすべて削除します">★ この1枚だけ残す</button>
                          <span style={{width: 1, height: 16, background: 'rgba(255,255,255,0.2)', margin: '0 2px'}} />
                          <button className="btn" style={{fontSize:12}} onClick={reorderByFilename} disabled={deleting || reordering}
                            title="ファイル名の番号順(01, 02... 10)で自動整列します">🔀 ファイル名順で整列</button>
                          <button className="btn" style={{fontSize:12}} onClick={reverseOrder} disabled={deleting || reordering}
                            title="ページの順序を前後に反転します">🔄 逆順</button>
                          <button className="btn" style={{fontSize:12}} onClick={() => moveCurrentImage(-1)} disabled={deleting || reordering || currentPreviewIdx === 0}
                            title="表示中の画像を1つ前(左)へ移動">◀ 前へ</button>
                          <button className="btn" style={{fontSize:12}} onClick={() => moveCurrentImage(1)} disabled={deleting || reordering || currentPreviewIdx >= previews.filter(p => !deletedPreviewIds.has(p.id)).length - 1}
                            title="表示中の画像を1つ後ろ(右)へ移動">次へ ▶</button>
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
                      deletedPreviewIds.has(p.id) ? (
                        <div key={p.id} className="timeline-thumb-deleted" title="削除済み">
                          <span aria-hidden="true">🗑</span>
                          <span>削除済み</span>
                        </div>
                      ) : (
                        <div key={p.id} style={{position:'relative', flex:'0 0 auto', lineHeight:0}}>
                          <img src={`/api/items/${previewItems[selectedIndex].id}/preview/?index=${p.index}`} alt={`preview-${p.index}`}
                            loading="lazy"
                            decoding="async"
                            className={currentPreviewIdx===p.index? 'timeline-thumb selected':'timeline-thumb'}
                            style={selectMode && pickedIds.has(p.id) ? {borderColor:'#3b82f6', opacity:1} : undefined}
                            title={p.filename ? `${p.index + 1}. ${p.filename}` : `ページ ${p.index + 1}`}
                            onClick={()=>{
                              if(selectMode){
                                togglePicked(p.id)
                              } else {
                                selectPreviewIndex(p.index)
                                if(readerLayout === 'comic'){
                                  const el = document.getElementById(`comic-page-${p.index}`)
                                  if(el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
                                }
                              }
                            }} />
                          {checkpointsMap.has(p.index) && (
                            <span
                              style={{
                                position: 'absolute',
                                top: 2,
                                right: 2,
                                background: '#2563eb',
                                color: '#fff',
                                fontSize: 9,
                                padding: '1px 3px',
                                borderRadius: 2,
                                pointerEvents: 'none',
                                lineHeight: 1,
                                boxShadow: '0 1px 3px rgba(0,0,0,0.5)'
                              }}
                              title={checkpointsMap.get(p.index).label}
                            >
                              🚩
                            </span>
                          )}
                          <span style={{position:'absolute', bottom:2, right:2, background:'rgba(0,0,0,0.65)', color:'#fff',
                            fontSize:9, padding:'1px 4px', borderRadius:2, pointerEvents:'none', lineHeight:1}}>
                            {p.index + 1}
                          </span>
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
              )}
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
