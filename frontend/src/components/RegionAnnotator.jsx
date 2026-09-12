import React, { useState, useEffect, useRef } from 'react'
import { saveCharacterRegions } from '../lib/itemFieldsApi'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

let _boxIdCounter = 0
function nextBoxId() { return `box-${++_boxIdCounter}` }

// Lets a human draw/confirm per-character bounding boxes across ALL of an
// item's images (not just one) — the ground-truth counterpart to
// train_character_classifier.py's automatic bootstrap pseudo-labeling (see
// Item.character_regions in models.py and that command's own
// _get_manual_labeled_rows). Every box lives in one flat `boxes` array
// tagged with which image it belongs to (`imageIndex`); switching the
// currently-displayed image only changes which subset is rendered/editable,
// it never discards work already done on another image — `save` always
// sends the full array regardless of which image happens to be showing.
//
// A box can carry more than one character name — person-detection
// sometimes merges two overlapping people (e.g. a hug pose) into a single
// box, and there was previously no way to record both identities for it.
//
// Two ways a box gets here:
//   1. "自動検出" — POSTs /detect_regions/ (tagger._detect_person_boxes) for
//      the currently-displayed image and seeds unlabeled candidate boxes.
//   2. Manual drag-to-draw on the image, for anything the detector missed.
// Boxes are tracked/edited in the ORIGINAL image's pixel-coordinate space
// (matching tagger._detect_person_boxes/_crop_with_padding exactly, so no
// translation is needed server-side) and only ever converted to/from the
// image's on-screen CSS size at render time and on mouse events.
export default function RegionAnnotator({ item, onSaved, onDirtyChange, boxesRef = null, showOwnActions = true, titles = null }) {
  const [images, setImages] = useState([])            // [{index, url, content_type}, ...]
  const [currentImageIndex, setCurrentImageIndex] = useState(null)  // which image is being viewed/edited right now
  const [boxes, setBoxes] = useState([])               // [{id, imageIndex, box:[x1,y1,x2,y2], characters:string[]}]
  const [naturalSize, setNaturalSize] = useState(null) // {width, height} of the currently-displayed image
  const [detecting, setDetecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [activeBoxId, setActiveBoxId] = useState(null)  // box whose character-picker popover is open
  // Shift/Ctrl/Cmd-click adds boxes here instead of opening the single-box
  // popover — lets one character name be applied to several regions at
  // once (e.g. the same repeated background character appearing in
  // multiple boxes), instead of opening each box's popover and picking the
  // same name over and over. Mutually exclusive with activeBoxId — only
  // one of the two picker UIs is ever shown at a time (see
  // toggleBoxSelection).
  const [selectedBoxIds, setSelectedBoxIds] = useState(new Set())
  const [charQuery, setCharQuery] = useState('')
  // Raw CharacterGroup list — filtered by title below (mirroring
  // CharacterPicker.jsx's own scoping exactly, see effectiveTitles/
  // matchingGroups), rather than flattened into one unscoped name pool like
  // this component used to do. That used to mean the popover suggested
  // every character across every title in the whole DB regardless of which
  // title(s) this item actually has, making manual typing the only practical
  // way to narrow it down — the `titles` prop (falling back to the item's
  // own `titles` field when not given an override) fixes that.
  const [groups, setGroups] = useState([])

  useEffect(() => {
    fetch('/api/character-groups/')
      .then(r => r.json()).then(d => {
        setGroups(Array.isArray(d) ? d : (d.results || []))
      }).catch(() => {})
  }, [])

  // `titles` lets an embedding parent (ItemQueuePanel) hand over the LIVE
  // (unsaved) title list being edited in ItemEditForm right now, instead of
  // only the item's last-saved `titles` field — same real-time-tracking
  // idea as ItemQueuePanel's own situationDraft. Standalone usage (e.g. the
  // mismatch tab's "ここで領域指定を続ける") passes nothing, so it just
  // falls back to the item's own titles.
  const effectiveTitles = (Array.isArray(titles) ? titles : (item.titles || [])).filter(Boolean)
  const matchingGroups = effectiveTitles.length > 0
    ? groups.filter(g => (g.titles || []).some(t => effectiveTitles.includes(t)))
    : []
  // Only actually restrict when it would narrow things down — same
  // reasoning as CharacterPicker.jsx's own `scoped`: most existing groups
  // haven't been retroactively linked to a title yet, so filtering to zero
  // matches would just hide every known character instead of helping.
  const scoped = effectiveTitles.length > 0 && matchingGroups.length > 0
  const visibleGroups = scoped ? matchingGroups : groups
  const groupCharNames = [...new Set(visibleGroups.flatMap(g => g.characters || []))]

  const imgRef = useRef(null)
  const containerRef = useRef(null)
  const drawStartRef = useRef(null)  // {x, y} in natural coords, while dragging
  const [drawRect, setDrawRect] = useState(null)  // live preview rect while dragging, natural coords

  // Close the single-box popover on any click outside it, instead of only
  // via its own "閉じる" button. Both the box itself and the popover's own
  // wrapper div stop propagation of BOTH mousedown and click (see below) —
  // mousedown specifically, not just click, because this listener fires on
  // mousedown: without stopping that too, clicking a suggestion button
  // inside the popover would close it (unmounting the button) before the
  // browser ever dispatches the follow-up click, so the button's own
  // onClick would silently never fire — reported as "clicking a character
  // in the list just closes the popover instead of picking it".
  useEffect(() => {
    if (!activeBoxId) return
    function handleOutsideMouseDown() { setActiveBoxId(null) }
    document.addEventListener('mousedown', handleOutsideMouseDown)
    return () => document.removeEventListener('mousedown', handleOutsideMouseDown)
  }, [activeBoxId])

  useEffect(() => {
    fetch(`/api/items/${item.id}/previews/`)
      .then(r => r.json()).then(d => {
        if (!Array.isArray(d)) return
        setImages(d)
        // Default to the first image only if nothing more specific was
        // already picked (e.g. by the pre-fill effect below restoring a
        // previously-labeled image_index).
        setCurrentImageIndex(prev => prev != null ? prev : (d[0] ? d[0].index : null))
      }).catch(() => {})
  }, [item.id])

  // Pre-fill from any previously saved labels for this item, so re-opening
  // an already-annotated item shows what's there (across every image it
  // was labeled on) instead of a blank slate.
  useEffect(() => {
    if (Array.isArray(item.character_regions) && item.character_regions.length > 0) {
      setBoxes(item.character_regions.map(r => ({
        id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box, characters: r.characters || [],
      })))
      setCurrentImageIndex(item.character_regions[0].image_index ?? null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const previewUrl = currentImageIndex != null
    ? `/api/items/${item.id}/preview/?index=${currentImageIndex}`
    : `/api/items/${item.id}/preview/`

  // Only the boxes drawn on whichever image is currently displayed —
  // boxes on other images stay tracked in `boxes` but aren't shown/editable
  // until the user switches to that image.
  const visibleBoxes = boxes.filter(b => b.imageIndex === currentImageIndex)

  function labeledCountFor(imageIndex) {
    return boxes.filter(b => b.imageIndex === imageIndex && b.characters.length > 0).length
  }

  function scale() {
    if (!imgRef.current || !naturalSize) return 1
    return imgRef.current.clientWidth / naturalSize.width
  }

  function toNaturalCoords(clientX, clientY) {
    const rect = containerRef.current.getBoundingClientRect()
    const s = scale()
    return {
      x: Math.round((clientX - rect.left) / s),
      y: Math.round((clientY - rect.top) / s),
    }
  }

  async function runDetect() {
    setDetecting(true)
    setError('')
    try {
      const resp = await fetch(`/api/items/${item.id}/detect_regions/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ image_index: currentImageIndex }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(j.detail || `自動検出に失敗しました (${resp.status})`)
      const resolvedIndex = j.image_index ?? null
      if (resolvedIndex !== currentImageIndex) setCurrentImageIndex(resolvedIndex)
      const detected = (j.boxes || []).map(box => ({ id: nextBoxId(), imageIndex: resolvedIndex, box, characters: [] }))
      setBoxes(prev => [...prev, ...detected])
      if (detected.length === 0) setNotice('人物が検出されませんでした。手動でドラッグして矩形を追加してください。')
      else { setNotice(''); if (onDirtyChange) onDirtyChange(true) }
    } catch (e) {
      setError(e.message)
    } finally {
      setDetecting(false)
    }
  }

  function handleMouseDown(e) {
    if (e.target !== containerRef.current && e.target !== imgRef.current) return  // clicked an existing box, not the background
    const { x, y } = toNaturalCoords(e.clientX, e.clientY)
    drawStartRef.current = { x, y }
    setDrawRect({ x1: x, y1: y, x2: x, y2: y })
  }

  function handleMouseMove(e) {
    if (!drawStartRef.current) return
    const { x, y } = toNaturalCoords(e.clientX, e.clientY)
    const start = drawStartRef.current
    setDrawRect({ x1: Math.min(start.x, x), y1: Math.min(start.y, y), x2: Math.max(start.x, x), y2: Math.max(start.y, y) })
  }

  function handleMouseUp() {
    if (!drawStartRef.current || !drawRect) { drawStartRef.current = null; setDrawRect(null); return }
    const { x1, y1, x2, y2 } = drawRect
    drawStartRef.current = null
    setDrawRect(null)
    if (x2 - x1 < 8 || y2 - y1 < 8) return  // too small — treat as an accidental click, not a real box
    const id = nextBoxId()
    setBoxes(prev => [...prev, { id, imageIndex: currentImageIndex, box: [x1, y1, x2, y2], characters: [] }])
    setActiveBoxId(id)
    setCharQuery('')
    if (onDirtyChange) onDirtyChange(true)
  }

  // Toggles `name` in/out of a box's character list — a box can hold more
  // than one label (see the component's own top-level comment), so this
  // never replaces the list or closes the popover, unlike a normal single-
  // select picker.
  function toggleCharacter(boxId, name) {
    const trimmed = name.trim()
    if (!trimmed) return
    setBoxes(prev => prev.map(b => {
      if (b.id !== boxId) return b
      const has = b.characters.includes(trimmed)
      return { ...b, characters: has ? b.characters.filter(c => c !== trimmed) : [...b.characters, trimmed] }
    }))
    setCharQuery('')
    if (onDirtyChange) onDirtyChange(true)
  }

  // Plain click: single-select this box (clearing any multi-selection) and
  // open its own popover, same as before. Shift/Ctrl/Cmd-click: toggle
  // this box in/out of the multi-selection instead, closing the single-box
  // popover — the two pickers are mutually exclusive.
  function toggleBoxSelection(boxId, ev) {
    if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
      setActiveBoxId(null)
      setSelectedBoxIds(prev => {
        const next = new Set(prev)
        if (next.has(boxId)) next.delete(boxId)
        else next.add(boxId)
        return next
      })
    } else {
      setSelectedBoxIds(new Set())
      setActiveBoxId(boxId)
    }
    setCharQuery('')
  }

  // Whether `name` is already assigned to EVERY currently multi-selected
  // box — drives the highlighted/checked look in the multi-select bar so
  // a mis-click is visible at a glance, the same way a single box's own
  // popover highlights its already-assigned names.
  function isAssignedToAllSelected(name) {
    if (selectedBoxIds.size === 0) return false
    return boxes.every(b => !selectedBoxIds.has(b.id) || b.characters.includes(name))
  }

  // Toggles `name` across every currently multi-selected box: if it's
  // already on ALL of them, clicking again removes it from all (undoing a
  // mis-assignment) — if it's missing from any, clicking adds it to
  // whichever selected boxes don't have it yet (the original "label all
  // of these the same" behavior). Symmetric with toggleCharacter's
  // single-box behavior, just applied to a whole selection at once.
  function toggleCharacterForSelectedBoxes(name) {
    const trimmed = name.trim()
    if (!trimmed || selectedBoxIds.size === 0) return
    const removeFromAll = isAssignedToAllSelected(trimmed)
    setBoxes(prev => prev.map(b => {
      if (!selectedBoxIds.has(b.id)) return b
      if (removeFromAll) return { ...b, characters: b.characters.filter(c => c !== trimmed) }
      if (b.characters.includes(trimmed)) return b
      return { ...b, characters: [...b.characters, trimmed] }
    }))
    setCharQuery('')
    if (onDirtyChange) onDirtyChange(true)
  }

  function removeBox(boxId) {
    setBoxes(prev => prev.filter(b => b.id !== boxId))
    if (activeBoxId === boxId) setActiveBoxId(null)
    setSelectedBoxIds(prev => {
      if (!prev.has(boxId)) return prev
      const next = new Set(prev)
      next.delete(boxId)
      return next
    })
    if (onDirtyChange) onDirtyChange(true)
  }

  // Only the labeled (has at least one character) boxes are ever sent —
  // unfinished/blank boxes just stay in local `boxes` state as a reminder,
  // never persisted. Shared by save() and boxesRef's getPayload() (for
  // ItemQueuePanel's own combined save orchestration) so both always agree
  // on exactly what "the regions to save" means.
  function labeledRegionsPayload() {
    return boxes.filter(b => b.characters.length > 0)
      .map(b => ({ image_index: b.imageIndex, box: b.box, characters: b.characters }))
  }

  // Publishes the save-ready payload for an embedding parent (ItemQueuePanel)
  // to read at save time only — a plain mutable ref, not forwardRef/
  // useImperativeHandle (this codebase's own established idiom for this —
  // see EditFields.jsx's ItemEditForm's own fieldsRef for the same pattern).
  useEffect(() => {
    if (!boxesRef) return
    boxesRef.current = { getPayload: labeledRegionsPayload }
  })

  async function save() {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const j = await saveCharacterRegions(item.id, labeledRegionsPayload())
      if (onDirtyChange) onDirtyChange(false)
      if (onSaved) onSaved(j.item)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const allKnownChars = [...new Set([...(item.characters || []), ...groupCharNames])]
  const charSuggestions = allKnownChars.filter(c =>
    !charQuery.trim() || c.toLowerCase().includes(charQuery.trim().toLowerCase())
  )
  const activeBox = boxes.find(b => b.id === activeBoxId) || null
  const totalLabeled = boxes.filter(b => b.characters.length > 0).length
  // Same hint text CharacterPicker.jsx shows for the identical situation —
  // explains why the suggestion list is showing every known character
  // instead of a title-scoped subset.
  const scopeHint = effectiveTitles.length === 0
    ? 'タイトル未選択のため全キャラを表示中'
    : (!scoped ? 'このタイトルに紐づくグループがないため全キャラを表示中' : null)

  return (
    <div>
      {error && <div style={{ color: '#f87171', marginBottom: 10, fontSize: 13 }}>{error}</div>}
      {notice && <div style={{ color: '#93c5fd', marginBottom: 10, fontSize: 13 }}>{notice}</div>}

      {images.length > 1 && (
        <div style={{ marginBottom: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {images.map(img => (
            <button key={img.index} className="btn" style={{ fontSize: 12 }}
              onClick={() => { setCurrentImageIndex(img.index); setSelectedBoxIds(new Set()) }}
              disabled={currentImageIndex === img.index}
            >
              {img.index + 1}枚目{currentImageIndex === img.index ? ' (表示中)' : ''}
              {labeledCountFor(img.index) > 0 && ` ✓${labeledCountFor(img.index)}`}
            </button>
          ))}
        </div>
      )}

      <div style={{ marginBottom: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn" onClick={runDetect} disabled={detecting}>
          {detecting ? '検出中…' : '🔍 自動検出'}
        </button>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          画像上をドラッグすると手動で矩形を追加できます。矩形をクリックしてキャラ名を割り当ててください(1つの矩形に複数のキャラを割り当てることもできます)。
          Shift(またはCtrl/Cmd)+クリックで複数の矩形を選択すると、同じキャラ名をまとめて割り当てられます。
        </span>
      </div>

      {selectedBoxIds.size > 0 && (
        <div style={{ marginBottom: 10, background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 13, color: '#f1f5f9', fontWeight: 600 }}>
              {selectedBoxIds.size}件の矩形を選択中 — 選んだキャラ名を全てに割り当てます(緑色=割り当て済み、もう一度クリックで取り消せます)
            </span>
            <button onClick={() => setSelectedBoxIds(new Set())}
              style={{ marginLeft: 'auto', fontSize: 11, color: '#94a3b8', background: 'none', border: 'none', cursor: 'pointer' }}
            >選択解除</button>
          </div>
          <input
            autoFocus
            placeholder="キャラ名で検索/新規入力"
            value={charQuery}
            onChange={e => setCharQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && charQuery.trim()) toggleCharacterForSelectedBoxes(charQuery) }}
            style={{
              width: '100%', boxSizing: 'border-box', background: '#0f172a', color: '#f1f5f9',
              border: '1px solid #334155', borderRadius: 4, padding: '6px 8px', fontSize: 13, marginBottom: 6,
            }}
          />
          {scopeHint && <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>{scopeHint}</div>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: 140, overflowY: 'auto' }}>
            {charSuggestions.slice(0, 20).map(c => {
              const assigned = isAssignedToAllSelected(c)
              return (
                <button key={c} onClick={() => toggleCharacterForSelectedBoxes(c)}
                  style={{
                    fontSize: 12, padding: '5px 10px', borderRadius: 4, border: 'none', cursor: 'pointer',
                    background: assigned ? '#166534' : '#334155', color: assigned ? '#dcfce7' : '#f1f5f9',
                  }}
                >{assigned ? '✓ ' : ''}{c}</button>
              )
            })}
            {charQuery.trim() && !charSuggestions.some(c => c.toLowerCase() === charQuery.trim().toLowerCase()) && (
              <button onClick={() => toggleCharacterForSelectedBoxes(charQuery)}
                style={{ fontSize: 12, padding: '5px 10px', borderRadius: 4, border: 'none', background: '#1e3a8a', color: '#93c5fd', cursor: 'pointer' }}
              >＋「{charQuery.trim()}」を新規追加</button>
            )}
          </div>
        </div>
      )}

      <div
        ref={containerRef}
        style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', cursor: 'crosshair', userSelect: 'none' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
      >
        <img
          ref={imgRef}
          src={previewUrl}
          alt={`item-${item.id}`}
          draggable={false}
          style={{ maxWidth: '100%', display: 'block' }}
          onLoad={e => setNaturalSize({ width: e.target.naturalWidth, height: e.target.naturalHeight })}
        />

        {naturalSize && visibleBoxes.map(b => {
          const s = scale()
          const [x1, y1, x2, y2] = b.box
          const isLabeled = b.characters.length > 0
          const isSelected = selectedBoxIds.has(b.id)
          return (
            <div key={b.id}
              onClick={ev => { ev.stopPropagation(); toggleBoxSelection(b.id, ev) }}
              onMouseDown={ev => ev.stopPropagation()}
              style={{
                position: 'absolute', left: x1 * s, top: y1 * s, width: (x2 - x1) * s, height: (y2 - y1) * s,
                border: `2px solid ${isLabeled ? '#22c55e' : '#f59e0b'}`,
                outline: isSelected ? '3px solid #3b82f6' : 'none',
                outlineOffset: 2,
                background: isSelected ? 'rgba(59,130,246,0.18)' : (isLabeled ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)'),
                cursor: 'pointer', boxSizing: 'border-box',
              }}
            >
              <span style={{
                position: 'absolute', top: -20, left: 0, fontSize: 11, padding: '1px 5px', borderRadius: 3,
                background: isLabeled ? '#166534' : '#78350f', color: '#fff', whiteSpace: 'nowrap',
                maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {b.characters.join('、') || '?'}
              </span>
              <button
                onClick={ev => { ev.stopPropagation(); removeBox(b.id) }}
                style={{
                  position: 'absolute', top: -20, right: 0, fontSize: 11, border: 'none', borderRadius: 3,
                  background: '#7f1d1d', color: '#fff', padding: '1px 5px', cursor: 'pointer', lineHeight: 1,
                }}
              >×</button>

              {activeBoxId === b.id && (
                <div onClick={ev => ev.stopPropagation()} onMouseDown={ev => ev.stopPropagation()} style={{
                  position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 10,
                  background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: 8,
                  width: 220, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
                }}>
                  {activeBox && activeBox.characters.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                      {activeBox.characters.map(c => (
                        <span key={c} style={{
                          display: 'inline-flex', alignItems: 'center', gap: 4, background: '#166534',
                          color: '#dcfce7', borderRadius: 4, padding: '2px 6px', fontSize: 12,
                        }}>
                          {c}
                          <button onClick={() => toggleCharacter(b.id, c)}
                            style={{ border: 'none', background: 'none', color: '#dcfce7', cursor: 'pointer', padding: 0, fontSize: 13, lineHeight: 1 }}
                          >×</button>
                        </span>
                      ))}
                    </div>
                  )}
                  <input
                    autoFocus
                    placeholder="キャラ名で検索/新規入力"
                    value={charQuery}
                    onChange={e => setCharQuery(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && charQuery.trim()) toggleCharacter(b.id, charQuery) }}
                    style={{
                      width: '100%', boxSizing: 'border-box', background: '#0f172a', color: '#f1f5f9',
                      border: '1px solid #334155', borderRadius: 4, padding: '6px 8px', fontSize: 13, marginBottom: 6,
                    }}
                  />
                  {scopeHint && <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>{scopeHint}</div>}
                  <div style={{ maxHeight: 140, overflowY: 'auto' }}>
                    {charSuggestions.slice(0, 20).map(c => {
                      const selected = b.characters.includes(c)
                      return (
                        <button key={c} onClick={() => toggleCharacter(b.id, c)}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 6, width: '100%', textAlign: 'left',
                            background: selected ? '#334155' : 'none', border: 'none',
                            color: '#f1f5f9', padding: '5px 6px', fontSize: 13, cursor: 'pointer', borderRadius: 4,
                          }}
                          onMouseEnter={e => { if (!selected) e.currentTarget.style.background = '#334155' }}
                          onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'none' }}
                        >
                          <span style={{ width: 14 }}>{selected ? '✓' : ''}</span>{c}
                        </button>
                      )
                    })}
                    {charQuery.trim() && !charSuggestions.some(c => c.toLowerCase() === charQuery.trim().toLowerCase()) && (
                      <button onClick={() => toggleCharacter(b.id, charQuery)}
                        style={{
                          display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none',
                          color: '#93c5fd', padding: '5px 6px', fontSize: 13, cursor: 'pointer',
                        }}
                      >＋「{charQuery.trim()}」を新規追加</button>
                    )}
                  </div>
                  <button onClick={() => setActiveBoxId(null)}
                    style={{ marginTop: 6, fontSize: 11, color: '#94a3b8', background: 'none', border: 'none', cursor: 'pointer' }}
                  >閉じる</button>
                </div>
              )}
            </div>
          )
        })}

        {drawRect && naturalSize && (() => {
          const s = scale()
          return (
            <div style={{
              position: 'absolute', left: drawRect.x1 * s, top: drawRect.y1 * s,
              width: (drawRect.x2 - drawRect.x1) * s, height: (drawRect.y2 - drawRect.y1) * s,
              border: '2px dashed #60a5fa', pointerEvents: 'none',
            }} />
          )
        })()}
      </div>

      <div style={{ marginTop: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
        {showOwnActions && (
          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '8px 20px', fontWeight: 600 }}
            onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存(全画像分をまとめて保存)'}
          </button>
        )}
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          {boxes.length === 0 ? '矩形がありません' : `全${images.length || 1}枚中 ${totalLabeled}/${boxes.length}件の矩形にキャラ名を割り当て済み(未割当の矩形は保存されません)`}
        </span>
      </div>
    </div>
  )
}
