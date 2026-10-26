import React, { useState, useEffect, useRef, useMemo } from 'react'
import { groupsForTitles } from '../lib/groupScope'
import { handleGroupingResponse } from '../lib/groupChoice'
import { saveCharacterRegions } from '../lib/itemFieldsApi'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

let _boxIdCounter = 0
function nextBoxId() { return `box-${++_boxIdCounter}` }

const CHAR_COLORS = [
  { border: '#3b82f6', bg: 'rgba(59, 130, 246, 0.14)', tag: '#1d4ed8' }, // blue
  { border: '#10b981', bg: 'rgba(16, 185, 129, 0.14)', tag: '#047857' }, // green
  { border: '#f59e0b', bg: 'rgba(245, 158, 11, 0.14)', tag: '#b45309' }, // amber
  { border: '#ec4899', bg: 'rgba(236, 72, 153, 0.14)', tag: '#be185d' }, // pink
  { border: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.14)', tag: '#6d28d9' }, // purple
  { border: '#06b6d4', bg: 'rgba(6, 182, 212, 0.14)', tag: '#0e7490' }, // cyan
  { border: '#f97316', bg: 'rgba(249, 115, 22, 0.14)', tag: '#c2410c' }, // orange
  { border: '#14b8a6', bg: 'rgba(20, 184, 166, 0.14)', tag: '#0f766e' }, // teal
]

function getCharColor(charName) {
  if (!charName) return null
  let hash = 0
  for (let i = 0; i < charName.length; i++) hash = (hash * 31 + charName.charCodeAt(i)) | 0
  return CHAR_COLORS[Math.abs(hash) % CHAR_COLORS.length]
}

export default function RegionAnnotator({ item, onSaved, onDirtyChange, boxesRef = null, showOwnActions = true, titles = null, proposedRegions = null }) {
  const [images, setImages] = useState([])            // [{index, url, content_type}, ...]
  const [currentImageIndex, setCurrentImageIndex] = useState(null)  // which image is being viewed/edited right now
  const [boxes, setBoxes] = useState([])               // [{id, imageIndex, box:[x1,y1,x2,y2], characters:string[]}]
  const [naturalSize, setNaturalSize] = useState(null) // {width, height} of the currently-displayed image
  const [detecting, setDetecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [activeBoxId, setActiveBoxId] = useState(null)  // box whose character-picker popover is open
  const [selectedBoxIds, setSelectedBoxIds] = useState(new Set())
  const [charQuery, setCharQuery] = useState('')
  const [groups, setGroups] = useState([])
  // Stamp mode: when set, clicking any box directly assigns this character without opening the popover
  const [stampChar, setStampChar] = useState(null)

  useEffect(() => {
    fetch('/api/character-groups/')
      .then(r => r.json()).then(d => {
        setGroups(Array.isArray(d) ? d : (d.results || []))
      }).catch(() => {})
  }, [])

  const effectiveTitles = (Array.isArray(titles) ? titles : (item.titles || [])).filter(Boolean)
  const matchingGroups = groupsForTitles(groups, effectiveTitles)
  const scoped = effectiveTitles.length > 0 && matchingGroups.length > 0
  const visibleGroups = scoped ? matchingGroups : groups
  const groupCharNames = [...new Set(visibleGroups.flatMap(g => g.characters || []))]

  const imgRef = useRef(null)
  const containerRef = useRef(null)
  const drawStartRef = useRef(null)
  const [drawRect, setDrawRect] = useState(null)

  // Close the single-box popover on click outside
  useEffect(() => {
    if (!activeBoxId) return
    function handleOutsideMouseDown() { setActiveBoxId(null) }
    document.addEventListener('mousedown', handleOutsideMouseDown)
    return () => document.removeEventListener('mousedown', handleOutsideMouseDown)
  }, [activeBoxId])

  // Cancel modes on Escape key
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape') {
        if (stampChar) setStampChar(null)
        if (activeBoxId) setActiveBoxId(null)
        if (selectedBoxIds.size > 0) setSelectedBoxIds(new Set())
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [stampChar, activeBoxId, selectedBoxIds.size])

  useEffect(() => {
    fetch(`/api/items/${item.id}/previews/`)
      .then(r => r.json()).then(d => {
        if (!Array.isArray(d)) return
        setImages(d)
        setCurrentImageIndex(prev => prev != null ? prev : (d[0] ? d[0].index : null))
      }).catch(() => {})
  }, [item.id])

  // Pre-fill from any previously saved labels for this item
  useEffect(() => {
    if (Array.isArray(item.character_regions) && item.character_regions.length > 0) {
      setBoxes(item.character_regions.map(r => ({
        id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box, characters: r.characters || [],
      })))
      setCurrentImageIndex(item.character_regions[0].image_index ?? null)
    } else if (Array.isArray(proposedRegions) && proposedRegions.length > 0) {
      setBoxes(proposedRegions.map(r => ({
        id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box, characters: r.characters || [],
      })))
      if (proposedRegions[0].image_index != null) {
        setCurrentImageIndex(proposedRegions[0].image_index)
      }
      setNotice('AI提案の領域とキャラクター候補を自動セットしました。合っていればそのまま保存、違っていれば修正できます。')
      if (onDirtyChange) onDirtyChange(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if ((!item.character_regions || item.character_regions.length === 0) &&
        Array.isArray(proposedRegions) && proposedRegions.length > 0) {
      setBoxes(prev => {
        if (prev.length > 0) return prev
        const next = proposedRegions.map(r => ({
          id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box, characters: r.characters || [],
        }))
        if (proposedRegions[0].image_index != null) {
          setCurrentImageIndex(proposedRegions[0].image_index)
        }
        setNotice('AI提案の領域とキャラクター候補を自動セットしました。合っていればそのまま保存、違っていれば修正できます。')
        if (onDirtyChange) onDirtyChange(true)
        return next
      })
    }
  }, [proposedRegions, item.character_regions, onDirtyChange])

  const previewUrl = currentImageIndex != null
    ? `/api/items/${item.id}/preview/?index=${currentImageIndex}`
    : `/api/items/${item.id}/preview/`

  const visibleBoxes = boxes.filter(b => b.imageIndex === currentImageIndex)

  // Character counts per box on current image
  const currentCharCounts = useMemo(() => {
    const counts = {}
    for (const b of visibleBoxes) {
      for (const c of b.characters) counts[c] = (counts[c] || 0) + 1
    }
    return counts
  }, [visibleBoxes])

  // Character counts across all boxes of this item
  const allAssignedCharCounts = useMemo(() => {
    const counts = {}
    for (const b of boxes) {
      for (const c of b.characters) counts[c] = (counts[c] || 0) + 1
    }
    return counts
  }, [boxes])

  // Frequently used / already assigned characters for quick one-click picking
  const quickPickChars = useMemo(() => {
    return Object.entries(allAssignedCharCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([name]) => name)
  }, [allAssignedCharCounts])

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
      let detected = []
      if (Array.isArray(j.proposed_regions) && j.proposed_regions.length > 0) {
        detected = j.proposed_regions.map(r => ({
          id: nextBoxId(), imageIndex: r.image_index ?? resolvedIndex, box: r.box, characters: r.characters || [],
        }))
      } else {
        detected = (j.boxes || []).map(box => ({ id: nextBoxId(), imageIndex: resolvedIndex, box, characters: [] }))
      }
      setBoxes(prev => [...prev, ...detected])
      if (detected.length === 0) setNotice('人物が検出されませんでした。手動でドラッグして矩形を追加してください。')
      else {
        const hasChars = detected.some(d => d.characters.length > 0)
        setNotice(hasChars ? '人物領域と候補キャラクターを検出しました。同一キャラの複数回登場も考慮されています。合っていればそのまま保存、違っていれば修正できます。' : '')
        if (onDirtyChange) onDirtyChange(true)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setDetecting(false)
    }
  }

  function handleMouseDown(e) {
    if (e.target !== containerRef.current && e.target !== imgRef.current) return
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
    if (x2 - x1 < 8 || y2 - y1 < 8) return
    const id = nextBoxId()
    const initialChars = stampChar ? [stampChar] : []
    setBoxes(prev => [...prev, { id, imageIndex: currentImageIndex, box: [x1, y1, x2, y2], characters: initialChars }])
    if (!stampChar) {
      setActiveBoxId(id)
    }
    setCharQuery('')
    if (onDirtyChange) onDirtyChange(true)
  }

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

  function handleBoxClick(boxId, ev) {
    ev.stopPropagation()
    if (stampChar) {
      // Stamp mode active: toggle stamp character on this box directly!
      toggleCharacter(boxId, stampChar)
      return
    }
    toggleBoxSelection(boxId, ev)
  }

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

  function isAssignedToAllSelected(name) {
    if (selectedBoxIds.size === 0) return false
    return boxes.every(b => !selectedBoxIds.has(b.id) || b.characters.includes(name))
  }

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

  function labeledRegionsPayload() {
    return boxes.filter(b => b.characters.length > 0)
      .map(b => ({ image_index: b.imageIndex, box: b.box, characters: b.characters }))
  }

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
      await handleGroupingResponse(item.id, j)
      if (onSaved) onSaved(j.item)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const allKnownChars = [...new Set([...(item.characters || []), ...quickPickChars, ...groupCharNames])]
  const charSuggestions = allKnownChars.filter(c =>
    !charQuery.trim() || c.toLowerCase().includes(charQuery.trim().toLowerCase())
  )
  const activeBox = boxes.find(b => b.id === activeBoxId) || null
  const totalLabeled = boxes.filter(b => b.characters.length > 0).length

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

      {/* Stamp mode active banner */}
      {stampChar && (
        <div style={{ marginBottom: 10, background: '#1e3a8a', border: '1px solid #3b82f6', borderRadius: 6, padding: '8px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14 }}>📌</span>
            <span style={{ fontSize: 13, color: '#f1f5f9', fontWeight: 600 }}>
              連続スタンプモード: <span style={{ color: '#93c5fd' }}>{stampChar}</span>
            </span>
            <span style={{ fontSize: 12, color: '#bfdbfe' }}>
              （矩形をクリックすると「{stampChar}」を次々割り当て/解除できます。同じキャラが何回も登場するコマの入力に便利です）
            </span>
          </div>
          <button
            type="button"
            className="btn"
            style={{ fontSize: 11, padding: '3px 8px', background: '#3b82f6', color: '#fff' }}
            onClick={() => setStampChar(null)}
          >
            ✕ スタンプ終了 (Esc)
          </button>
        </div>
      )}

      <div style={{ marginBottom: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" onClick={runDetect} disabled={detecting}>
          {detecting ? '検出中…' : '🔍 自動検出'}
        </button>
        {visibleBoxes.length > 0 && (
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              type="button"
              className="btn"
              style={{ fontSize: 11, padding: '3px 8px' }}
              onClick={() => setSelectedBoxIds(new Set(visibleBoxes.map(b => b.id)))}
              title="この画像のすべての矩形を選択します"
            >
              全矩形を選択 ({visibleBoxes.length})
            </button>
            <button
              type="button"
              className="btn"
              style={{ fontSize: 11, padding: '3px 8px' }}
              onClick={() => setSelectedBoxIds(new Set(visibleBoxes.filter(b => b.characters.length === 0).map(b => b.id)))}
              disabled={visibleBoxes.filter(b => b.characters.length === 0).length === 0}
              title="まだキャラが割り当てられていない矩形をまとめて選択します"
            >
              未割当を選択 ({visibleBoxes.filter(b => b.characters.length === 0).length})
            </button>
          </div>
        )}
        {quickPickChars.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 'auto', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: '#94a3b8' }}>スタンプ割当:</span>
            {quickPickChars.slice(0, 5).map(c => (
              <button
                key={c}
                type="button"
                className="btn"
                style={{
                  fontSize: 11, padding: '2px 7px',
                  background: stampChar === c ? '#2563eb' : '#1e293b',
                  color: stampChar === c ? '#fff' : '#cbd5e1',
                  border: stampChar === c ? '1px solid #60a5fa' : '1px solid #475569',
                }}
                onClick={() => setStampChar(prev => prev === c ? null : c)}
                title={`クリックすると「${c}」の連続割り当て(スタンプ)モードになります`}
              >
                📌 {c}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={{ marginBottom: 10, fontSize: 12, color: '#94a3b8' }}>
        画像上をドラッグすると手動で矩形を追加できます。矩形をクリックしてキャラ名を割り当ててください(1画像内に同じキャラが何回登場してもそれぞれ指定できます)。
        Shift/Ctrl+クリックで複数選択、または「📌 スタンプ割当」で同じキャラを連続クリックして一括入力できます。
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

          {/* Quick pick for multi-selection */}
          {quickPickChars.length > 0 && (
            <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11, color: '#94a3b8' }}>⚡ よく使うキャラ:</span>
              {quickPickChars.map(c => {
                const assigned = isAssignedToAllSelected(c)
                return (
                  <button
                    key={c}
                    type="button"
                    onClick={() => toggleCharacterForSelectedBoxes(c)}
                    style={{
                      fontSize: 11, padding: '3px 8px', borderRadius: 4, border: 'none', cursor: 'pointer',
                      background: assigned ? '#166534' : '#334155', color: assigned ? '#dcfce7' : '#f1f5f9',
                    }}
                  >
                    {assigned ? '✓ ' : '+ '}{c}
                  </button>
                )
              })}
            </div>
          )}

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
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: 260, overflowY: 'auto' }}>
            {charSuggestions.map(c => {
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
        style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', cursor: stampChar ? 'copy' : 'crosshair', userSelect: 'none' }}
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

        {naturalSize && visibleBoxes.map((b) => {
          const s = scale()
          const [x1, y1, x2, y2] = b.box
          const isLabeled = b.characters.length > 0
          const isSelected = selectedBoxIds.has(b.id)
          const firstChar = b.characters[0]
          const charColor = isLabeled ? getCharColor(firstChar) : null
          const totalSameChar = firstChar ? (currentCharCounts[firstChar] || 0) : 0

          let occurrenceIdx = 0
          if (firstChar && totalSameChar > 1) {
            const sameCharBoxes = visibleBoxes.filter(x => x.characters.includes(firstChar))
            occurrenceIdx = sameCharBoxes.findIndex(x => x.id === b.id) + 1
          }

          const borderColor = isSelected
            ? '#3b82f6'
            : (charColor ? charColor.border : (isLabeled ? '#22c55e' : '#f59e0b'))
          const bgColor = isSelected
            ? 'rgba(59,130,246,0.22)'
            : (charColor ? charColor.bg : (isLabeled ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)'))
          const tagBg = isSelected
            ? '#2563eb'
            : (charColor ? charColor.tag : (isLabeled ? '#166534' : '#78350f'))

          const labelText = b.characters.length > 0
            ? b.characters.map(c => {
                const count = currentCharCounts[c] || 0
                return count > 1 && occurrenceIdx > 0 ? `${c} #${occurrenceIdx}` : c
              }).join('、')
            : '?'

          return (
            <div key={b.id}
              onClick={ev => handleBoxClick(b.id, ev)}
              onMouseDown={ev => ev.stopPropagation()}
              style={{
                position: 'absolute', left: x1 * s, top: y1 * s, width: (x2 - x1) * s, height: (y2 - y1) * s,
                border: `2px solid ${borderColor}`,
                outline: isSelected ? '3px solid #3b82f6' : (stampChar ? '1px dashed #60a5fa' : 'none'),
                outlineOffset: 2,
                background: bgColor,
                cursor: stampChar ? 'copy' : 'pointer', boxSizing: 'border-box',
                transition: 'border-color 0.1s ease, background 0.1s ease',
              }}
              title={stampChar ? `クリックして「${stampChar}」を割り当て/解除` : undefined}
            >
              <span style={{
                position: 'absolute', top: -20, left: 0, fontSize: 11, padding: '1px 5px', borderRadius: 3,
                background: tagBg, color: '#fff', whiteSpace: 'nowrap',
                maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis',
                display: 'inline-flex', alignItems: 'center', gap: 4,
              }}>
                <span>{labelText}</span>
                {totalSameChar > 1 && (
                  <span style={{ fontSize: 9, opacity: 0.8, background: 'rgba(0,0,0,0.3)', padding: '0 3px', borderRadius: 2 }}>
                    計{totalSameChar}
                  </span>
                )}
              </span>
              <button
                onClick={ev => { ev.stopPropagation(); removeBox(b.id) }}
                style={{
                  position: 'absolute', top: -20, right: 0, fontSize: 11, border: 'none', borderRadius: 3,
                  background: '#7f1d1d', color: '#fff', padding: '1px 5px', cursor: 'pointer', lineHeight: 1,
                }}
                title="この矩形を削除"
              >×</button>

              {activeBoxId === b.id && (
                <div onClick={ev => ev.stopPropagation()} onMouseDown={ev => ev.stopPropagation()} style={{
                  position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 10,
                  background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: 8,
                  width: 250, boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
                }}>
                  {/* Quick Pick: Already assigned characters */}
                  {quickPickChars.length > 0 && (
                    <div style={{ marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #334155' }}>
                      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                        <span>⚡ 指定済みのキャラ (ワンクリック割当):</span>
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {quickPickChars.map(c => {
                          const selected = b.characters.includes(c)
                          const count = allAssignedCharCounts[c] || 0
                          const color = getCharColor(c)
                          return (
                            <button
                              key={c}
                              type="button"
                              onClick={() => toggleCharacter(b.id, c)}
                              style={{
                                fontSize: 11, padding: '3px 7px', borderRadius: 4, border: 'none', cursor: 'pointer',
                                background: selected ? (color ? color.tag : '#166534') : '#334155',
                                color: selected ? '#fff' : '#f1f5f9',
                                display: 'inline-flex', alignItems: 'center', gap: 3,
                              }}
                              title={`${c} (${count}箇所で割り当て中。クリックでこの矩形に割当/解除)`}
                            >
                              <span>{selected ? '✓' : '+'}</span>
                              <span>{c}</span>
                              {count > 1 && (
                                <span style={{ fontSize: 9, opacity: 0.8, background: 'rgba(0,0,0,0.25)', padding: '0 3px', borderRadius: 2 }}>
                                  {count}
                                </span>
                              )}
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  )}

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
                            title="このキャラを解除"
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
                  <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                    {charSuggestions.map(c => {
                      const selected = b.characters.includes(c)
                      return (
                        <div key={c} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderRadius: 4, background: selected ? '#334155' : 'transparent' }}>
                          <button onClick={() => toggleCharacter(b.id, c)}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 6, flex: 1, textAlign: 'left',
                              background: 'none', border: 'none',
                              color: '#f1f5f9', padding: '5px 6px', fontSize: 13, cursor: 'pointer',
                            }}
                          >
                            <span style={{ width: 14 }}>{selected ? '✓' : ''}</span>
                            <span>{c}</span>
                            {allAssignedCharCounts[c] > 0 && (
                              <span style={{ fontSize: 10, color: '#94a3b8', marginLeft: 'auto', marginRight: 4 }}>
                                ({allAssignedCharCounts[c]}箇所)
                              </span>
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setStampChar(c); setActiveBoxId(null) }}
                            style={{ border: 'none', background: 'transparent', color: '#60a5fa', cursor: 'pointer', padding: '4px 6px', fontSize: 11 }}
                            title={`「${c}」の連続割り当てスタンプモードにする`}
                          >
                            📌
                          </button>
                        </div>
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
                  <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <button onClick={() => setActiveBoxId(null)}
                      style={{ fontSize: 11, color: '#94a3b8', background: 'none', border: 'none', cursor: 'pointer' }}
                    >閉じる</button>
                    {b.characters.length > 0 && (
                      <button
                        type="button"
                        onClick={() => { setStampChar(b.characters[0]); setActiveBoxId(null) }}
                        style={{ fontSize: 11, color: '#60a5fa', background: 'none', border: 'none', cursor: 'pointer' }}
                        title="このキャラで別の矩形も次々指定する"
                      >
                        📌 連続スタンプ
                      </button>
                    )}
                  </div>
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

      <div style={{ marginTop: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {showOwnActions && (
          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '8px 20px', fontWeight: 600 }}
            onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存(全画像分をまとめて保存)'}
          </button>
        )}
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          {boxes.length === 0 ? '矩形がありません' : (
            <>
              <span>全{images.length || 1}枚中 {totalLabeled}/{boxes.length}件の矩形にキャラ名を割り当て済み</span>
              {Object.keys(allAssignedCharCounts).length > 0 && (
                <span style={{ marginLeft: 8, color: '#cbd5e1' }}>
                  ({Object.entries(allAssignedCharCounts).map(([c, cnt]) => `${c}: ${cnt}箇所`).join('、')})
                </span>
              )}
            </>
          )}
        </span>
      </div>
    </div>
  )
}
