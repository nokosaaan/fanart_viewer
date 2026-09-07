import React, { useState, useEffect, useRef } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

let _boxIdCounter = 0
function nextBoxId() { return `box-${++_boxIdCounter}` }

// Lets a human draw/confirm per-character bounding boxes on one image of a
// multi-character item — the ground-truth counterpart to
// train_character_classifier.py's automatic bootstrap pseudo-labeling (see
// Item.character_regions in models.py and that command's own
// _get_manual_labeled_rows). Two ways a box gets here:
//   1. "自動検出" — POSTs /detect_regions/ (tagger._detect_person_boxes) and
//      seeds unlabeled candidate boxes for the user to just assign names to.
//   2. Manual drag-to-draw on the image, for anything the detector missed.
// Boxes are tracked/edited in the ORIGINAL image's pixel-coordinate space
// (matching tagger._detect_person_boxes/_crop_with_padding exactly, so no
// translation is needed server-side) and only ever converted to/from the
// image's on-screen CSS size at render time and on mouse events.
export default function RegionAnnotator({ item, onSaved }) {
  const [images, setImages] = useState([])            // [{index, url, content_type}, ...]
  const [selectedImageIndex, setSelectedImageIndex] = useState(null)  // null = auto (largest)
  const [resolvedImageIndex, setResolvedImageIndex] = useState(null)  // which index is actually displayed
  const [boxes, setBoxes] = useState([])               // [{id, box:[x1,y1,x2,y2], character:string|null}]
  const [naturalSize, setNaturalSize] = useState(null) // {width, height}
  const [detecting, setDetecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [activeBoxId, setActiveBoxId] = useState(null)  // box whose character-picker popover is open
  const [charQuery, setCharQuery] = useState('')

  const imgRef = useRef(null)
  const containerRef = useRef(null)
  const drawStartRef = useRef(null)  // {x, y} in natural coords, while dragging
  const [drawRect, setDrawRect] = useState(null)  // live preview rect while dragging, natural coords

  useEffect(() => {
    fetch(`/api/items/${item.id}/previews/`)
      .then(r => r.json()).then(d => { if (Array.isArray(d)) setImages(d) }).catch(() => {})
  }, [item.id])

  // Pre-fill from any previously saved labels for this item, so re-opening
  // an already-annotated item shows what's there instead of a blank slate.
  useEffect(() => {
    if (item.character_regions_image_index != null) setSelectedImageIndex(item.character_regions_image_index)
    if (Array.isArray(item.character_regions) && item.character_regions.length > 0) {
      setResolvedImageIndex(item.character_regions_image_index ?? null)
      setBoxes(item.character_regions.map(r => ({ id: nextBoxId(), box: r.box, character: r.character })))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const previewUrl = resolvedImageIndex != null
    ? `/api/items/${item.id}/preview/?index=${resolvedImageIndex}`
    : `/api/items/${item.id}/preview/`

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
        body: JSON.stringify({ image_index: selectedImageIndex }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(j.detail || `自動検出に失敗しました (${resp.status})`)
      setResolvedImageIndex(j.image_index ?? null)
      const detected = (j.boxes || []).map(box => ({ id: nextBoxId(), box, character: null }))
      setBoxes(prev => [...prev, ...detected])
      if (detected.length === 0) setNotice('人物が検出されませんでした。手動でドラッグして矩形を追加してください。')
      else setNotice('')
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
    setBoxes(prev => [...prev, { id, box: [x1, y1, x2, y2], character: null }])
    setActiveBoxId(id)
    setCharQuery('')
  }

  function assignCharacter(boxId, name) {
    const trimmed = name.trim()
    if (!trimmed) return
    setBoxes(prev => prev.map(b => b.id === boxId ? { ...b, character: trimmed } : b))
    setActiveBoxId(null)
    setCharQuery('')
  }

  function removeBox(boxId) {
    setBoxes(prev => prev.filter(b => b.id !== boxId))
    if (activeBoxId === boxId) setActiveBoxId(null)
  }

  async function save() {
    const labeled = boxes.filter(b => b.character)
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const resp = await fetch(`/api/items/${item.id}/character_regions/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({
          image_index: resolvedImageIndex,
          regions: labeled.map(b => ({ box: b.box, character: b.character })),
        }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(j.detail || `保存に失敗しました (${resp.status})`)
      if (onSaved) onSaved(j.item)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const charSuggestions = (item.characters || []).filter(c =>
    !charQuery.trim() || c.toLowerCase().includes(charQuery.trim().toLowerCase())
  )

  return (
    <div>
      {error && <div style={{ color: '#f87171', marginBottom: 10, fontSize: 13 }}>{error}</div>}
      {notice && <div style={{ color: '#93c5fd', marginBottom: 10, fontSize: 13 }}>{notice}</div>}

      {images.length > 1 && (
        <div style={{ marginBottom: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {images.map(img => (
            <button key={img.index} className="btn" style={{ fontSize: 12 }}
              onClick={() => { setSelectedImageIndex(img.index); setResolvedImageIndex(img.index); setBoxes([]) }}
              disabled={resolvedImageIndex === img.index}
            >{img.index + 1}枚目{resolvedImageIndex === img.index ? ' (選択中)' : ''}</button>
          ))}
        </div>
      )}

      <div style={{ marginBottom: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn" onClick={runDetect} disabled={detecting}>
          {detecting ? '検出中…' : '🔍 自動検出'}
        </button>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          画像上をドラッグすると手動で矩形を追加できます。矩形をクリックしてキャラ名を割り当ててください。
        </span>
      </div>

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

        {naturalSize && boxes.map(b => {
          const s = scale()
          const [x1, y1, x2, y2] = b.box
          return (
            <div key={b.id}
              onClick={ev => { ev.stopPropagation(); setActiveBoxId(b.id); setCharQuery('') }}
              style={{
                position: 'absolute', left: x1 * s, top: y1 * s, width: (x2 - x1) * s, height: (y2 - y1) * s,
                border: `2px solid ${b.character ? '#22c55e' : '#f59e0b'}`,
                background: b.character ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)',
                cursor: 'pointer', boxSizing: 'border-box',
              }}
            >
              <span style={{
                position: 'absolute', top: -20, left: 0, fontSize: 11, padding: '1px 5px', borderRadius: 3,
                background: b.character ? '#166534' : '#78350f', color: '#fff', whiteSpace: 'nowrap',
              }}>
                {b.character || '?'}
              </span>
              <button
                onClick={ev => { ev.stopPropagation(); removeBox(b.id) }}
                style={{
                  position: 'absolute', top: -20, right: 0, fontSize: 11, border: 'none', borderRadius: 3,
                  background: '#7f1d1d', color: '#fff', padding: '1px 5px', cursor: 'pointer', lineHeight: 1,
                }}
              >×</button>

              {activeBoxId === b.id && (
                <div onClick={ev => ev.stopPropagation()} style={{
                  position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 10,
                  background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: 8,
                  width: 200, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
                }}>
                  <input
                    autoFocus
                    placeholder="キャラ名で検索/新規入力"
                    value={charQuery}
                    onChange={e => setCharQuery(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && charQuery.trim()) assignCharacter(b.id, charQuery) }}
                    style={{
                      width: '100%', boxSizing: 'border-box', background: '#0f172a', color: '#f1f5f9',
                      border: '1px solid #334155', borderRadius: 4, padding: '6px 8px', fontSize: 13, marginBottom: 6,
                    }}
                  />
                  <div style={{ maxHeight: 140, overflowY: 'auto' }}>
                    {charSuggestions.slice(0, 20).map(c => (
                      <button key={c} onClick={() => assignCharacter(b.id, c)}
                        style={{
                          display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none',
                          color: '#f1f5f9', padding: '5px 6px', fontSize: 13, cursor: 'pointer', borderRadius: 4,
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = '#334155'}
                        onMouseLeave={e => e.currentTarget.style.background = 'none'}
                      >{c}</button>
                    ))}
                    {charQuery.trim() && !charSuggestions.some(c => c.toLowerCase() === charQuery.trim().toLowerCase()) && (
                      <button onClick={() => assignCharacter(b.id, charQuery)}
                        style={{
                          display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none',
                          color: '#93c5fd', padding: '5px 6px', fontSize: 13, cursor: 'pointer',
                        }}
                      >＋「{charQuery.trim()}」を新規追加</button>
                    )}
                  </div>
                  <button onClick={() => setActiveBoxId(null)}
                    style={{ marginTop: 6, fontSize: 11, color: '#94a3b8', background: 'none', border: 'none', cursor: 'pointer' }}
                  >キャンセル</button>
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
        <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '8px 20px', fontWeight: 600 }}
          onClick={save} disabled={saving}>
          {saving ? '保存中…' : '保存'}
        </button>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          {boxes.length === 0 ? '矩形がありません' : `${boxes.filter(b => b.character).length}/${boxes.length}件にキャラ名を割り当て済み(未割当の矩形は保存されません)`}
        </span>
      </div>
    </div>
  )
}
