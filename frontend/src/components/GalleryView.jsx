import React, { useEffect, useRef, useState } from 'react'
import { ItemRow } from './ScrollList'

// One-item-at-a-time full-screen browsing, toggled against ScrollList's
// card-list via App.jsx's viewMode — reuses ItemRow completely unchanged
// (same fetch/upload/salvage/edit/delete buttons, same state) and just
// restyles it via the `.gallery-mode` CSS (see styles.css: a grid-area
// reflow that makes the preview image dominate and stacks titles/artist/
// tags beside it) rather than forking a second copy of ItemRow's fairly
// large stateful logic.
export default function GalleryView({ items, readOnly, onEnqueueFetch, onOpenPreview, onAddFilter }){
  const [currentId, setCurrentId] = useState(items[0] ? items[0].id : null)

  // Keep pointing at the same item across an `items` refresh (a page turn,
  // a filter change, or just another item's own preview/edit updating
  // elsewhere producing a new array) — only actually jump to the first
  // item when the current one is genuinely no longer in the list.
  useEffect(() => {
    if (items.length === 0) { setCurrentId(null); return }
    if (!items.some(it => it && it.id === currentId)) setCurrentId(items[0].id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items])

  const currentIndex = items.findIndex(it => it && it.id === currentId)
  const current = currentIndex === -1 ? null : items[currentIndex]

  function prev(){
    if (items.length === 0) return
    const i = currentIndex <= 0 ? items.length - 1 : currentIndex - 1
    setCurrentId(items[i].id)
  }
  function next(){
    if (items.length === 0) return
    const i = currentIndex >= items.length - 1 ? 0 : currentIndex + 1
    setCurrentId(items[i].id)
  }

  // Same wheel/arrow-key navigation PreviewPane.jsx's own full-screen modal
  // already uses (accumulate wheel delta past a threshold, cooldown between
  // navigations) — kept identical so switching between "Preview Timeline"
  // and this view doesn't also change how browsing itself feels.
  useEffect(() => {
    function onKey(e){
      if (e.key === 'ArrowUp') prev()
      if (e.key === 'ArrowDown') next()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIndex, items])

  const wheelAccRef = useRef(0)
  const lastNavRef = useRef(0)
  useEffect(() => {
    function handleWheel(e){
      const delta = e.deltaY || e.deltaX || 0
      wheelAccRef.current += delta
      const now = Date.now()
      const THRESH = 80, COOLDOWN = 180
      if (Math.abs(wheelAccRef.current) > THRESH && (now - lastNavRef.current) > COOLDOWN) {
        if (wheelAccRef.current > 0) next(); else prev()
        wheelAccRef.current = 0
        lastNavRef.current = now
      }
    }
    window.addEventListener('wheel', handleWheel, { passive: true })
    return () => window.removeEventListener('wheel', handleWheel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIndex, items])

  if (items.length === 0) {
    return <div className="cgm-empty-hint">表示できるアイテムがありません</div>
  }
  if (!current) return null

  return (
    <div className="gallery-mode gallery-view">
      <div className="gallery-progress">{currentIndex + 1} / {items.length}</div>
      <div className="modal-edge modal-edge-left" onClick={prev} aria-label="Previous" />
      <div className="modal-edge modal-edge-right" onClick={next} aria-label="Next" />
      <ItemRow
        key={current.id} it={current} readOnly={readOnly}
        onEnqueueFetch={onEnqueueFetch} onOpenPreview={onOpenPreview} onAddFilter={onAddFilter}
      />
    </div>
  )
}
