import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { groupsForTitles } from '../lib/groupScope'
import { handleGroupingResponse } from '../lib/groupChoice'
import { saveCharacterRegions } from '../lib/itemFieldsApi'
import { createImagePixelSampler } from '../lib/colorInspect'
import ColorInspectLoupe from './ColorInspectLoupe'

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

function simplifyPolygon(points, minStep = 6) {
  if (!Array.isArray(points) || points.length <= 3) return points || []
  const out = [points[0]]
  for (let i = 1; i < points.length; i++) {
    const prev = out[out.length - 1]
    const dx = points[i][0] - prev[0]
    const dy = points[i][1] - prev[1]
    if (dx * dx + dy * dy >= minStep * minStep) {
      out.push(points[i])
    }
  }
  return out
}

function boundingBoxOfPolygon(points) {
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity
  for (const [x, y] of points) {
    if (x < x1) x1 = x
    if (y < y1) y1 = y
    if (x > x2) x2 = x
    if (y > y2) y2 = y
  }
  return [Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)]
}

export default function RegionAnnotator({
  item,
  onSaved,
  onDirtyChange,
  onCharactersChange = null,
  boxesRef = null,
  showOwnActions = true,
  titles = null,
  proposedRegions = null,
  compact = false,
}) {
  const [images, setImages] = useState([])            // [{index, url, content_type}, ...]
  const [currentImageIndex, setCurrentImageIndex] = useState(null)  // which image is being viewed/edited right now
  const [boxes, setBoxes] = useState([])               // [{id, imageIndex, box:[x1,y1,x2,y2], characters:string[]}]
  const [naturalSize, setNaturalSize] = useState(null) // {width, height} of the currently-displayed image
  const [displayWidth, setDisplayWidth] = useState(0)  // rendered width of <img> tracked via ResizeObserver
  const [detecting, setDetecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [activeBoxId, setActiveBoxId] = useState(null)  // box whose character-picker popover is open
  const [selectedBoxIds, setSelectedBoxIds] = useState(new Set())
  const [hoveredBoxId, setHoveredBoxId] = useState(null)
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
  const [drawMode, setDrawMode] = useState('box') // 'box' | 'lasso' | 'poly'
  const [drawRect, setDrawRect] = useState(null)
  const [lassoPoints, setLassoPoints] = useState(null) // [[x, y], ...] while dragging
  const [polyPoints, setPolyPoints] = useState([])     // [[x, y], ...] while clicking vertices
  const [polyHoverPt, setPolyHoverPt] = useState(null)

  // Close the single-box popover on click outside
  useEffect(() => {
    if (!activeBoxId) return
    function handleOutsideMouseDown() { setActiveBoxId(null) }
    document.addEventListener('mousedown', handleOutsideMouseDown)
    return () => document.removeEventListener('mousedown', handleOutsideMouseDown)
  }, [activeBoxId])

  // Cancel modes on Escape key, finalize polygon on Enter
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape') {
        if (polyPoints.length > 0) { setPolyPoints([]); setPolyHoverPt(null) }
        if (lassoPoints) setLassoPoints(null)
        if (stampChar) setStampChar(null)
        if (activeBoxId) setActiveBoxId(null)
        if (selectedBoxIds.size > 0) setSelectedBoxIds(new Set())
      } else if (e.key === 'Enter' && drawMode === 'poly' && polyPoints.length >= 3) {
        e.preventDefault()
        commitPolygon(polyPoints)
        setPolyPoints([])
        setPolyHoverPt(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [stampChar, activeBoxId, selectedBoxIds.size, drawMode, polyPoints, lassoPoints])

  const loadItemPreviews = useCallback((id) => {
    fetch(`/api/items/${id}/previews/`, { cache: 'no-store' })
      .then(r => r.json()).then(d => {
        if (!Array.isArray(d)) return
        setImages(d)
        setCurrentImageIndex(prev => (prev != null && d.some(img => img.index === prev)) ? prev : (d[0] ? d[0].index : null))
      }).catch(() => {})
  }, [])

  const colorSamplerRef = useRef(null)
  if (!colorSamplerRef.current) colorSamplerRef.current = createImagePixelSampler()
  const [hoverColor, setHoverColor] = useState(null)
  const [cursorPos, setCursorPos] = useState(null)
  const [loupeEnabled, setLoupeEnabled] = useState(true)

  useEffect(() => {
    loadItemPreviews(item.id)
  }, [item.id, item.preview_count, item.first_preview_id, loadItemPreviews])

  useEffect(() => {
    function onPreviewUpdated(ev) {
      if (ev && ev.detail && ev.detail.id === item.id) {
        loadItemPreviews(item.id)
        fetch(`/api/items/${item.id}/`, { cache: 'no-store' })
          .then(r => r.json())
          .then(fresh => {
            if (fresh && Array.isArray(fresh.character_regions)) {
              setBoxes(fresh.character_regions.map(r => ({
                id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box,
                polygon: Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : null,
                characters: r.characters || [],
              })))
              setSelectedBoxIds(new Set())
              setActiveBoxId(null)
            }
          }).catch(() => {})
      }
    }
    window.addEventListener('item-preview-updated', onPreviewUpdated)
    return () => window.removeEventListener('item-preview-updated', onPreviewUpdated)
  }, [item.id, loadItemPreviews])

  // Pre-fill from any previously saved labels for this item
  useEffect(() => {
    if (Array.isArray(item.character_regions) && item.character_regions.length > 0) {
      setBoxes(item.character_regions.map(r => ({
        id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box,
        polygon: Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : null,
        characters: r.characters || [],
      })))
      setCurrentImageIndex(item.character_regions[0].image_index ?? null)
    } else if (Array.isArray(proposedRegions) && proposedRegions.length > 0) {
      setBoxes(proposedRegions.map(r => ({
        id: nextBoxId(), imageIndex: r.image_index ?? null, box: r.box,
        polygon: Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : null,
        characters: r.characters || [],
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

  const currentImageObj = currentImageIndex != null
    ? images.find(img => img.index === currentImageIndex)
    : images[0]
  const previewUrl = currentImageObj && currentImageObj.id != null
    ? `/api/items/${item.id}/preview/?id=${currentImageObj.id}`
    : (currentImageIndex != null
      ? `/api/items/${item.id}/preview/?index=${currentImageIndex}`
      : (item.first_preview_id != null
        ? `/api/items/${item.id}/preview/?id=${item.first_preview_id}`
        : `/api/items/${item.id}/preview/`))

  useEffect(() => {
    const img = imgRef.current
    if (!img) return
    const syncWidth = () => {
      if (img.clientWidth > 0) setDisplayWidth(img.clientWidth)
    }
    if (img.complete && img.naturalWidth > 0) {
      setNaturalSize({ width: img.naturalWidth, height: img.naturalHeight })
      syncWidth()
    }
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(syncWidth)
      ro.observe(img)
      return () => ro.disconnect()
    }
    window.addEventListener('resize', syncWidth)
    return () => window.removeEventListener('resize', syncWidth)
  }, [previewUrl])

  const visibleBoxes = boxes.filter(b => b.imageIndex === currentImageIndex)

  // Sort boxes so that smaller boxes appear in front (higher z-index and later DOM order)
  // and larger background boxes stay behind, allowing smaller inner boxes to be easily
  // clicked, edited, or deleted without being blocked by outer large boxes.
  const sortedVisibleBoxes = useMemo(() => {
    return [...visibleBoxes].sort((a, b) => {
      const areaA = Math.abs((a.box[2] - a.box[0]) * (a.box[3] - a.box[1]))
      const areaB = Math.abs((b.box[2] - b.box[0]) * (b.box[3] - b.box[1]))
      if (areaB !== areaA) return areaB - areaA // Larger first, smaller last (drawn on top)
      return a.id.localeCompare(b.id)
    })
  }, [visibleBoxes])

  // Detect boxes that lie completely outside the visible image (e.g. after image resize)
  const outOfBoundsBoxIds = useMemo(() => {
    if (!naturalSize) return new Set()
    const ids = new Set()
    for (const b of visibleBoxes) {
      const [x1, y1, x2, y2] = b.box
      if (
        x1 >= naturalSize.width || y1 >= naturalSize.height ||
        x2 <= 0 || y2 <= 0 || x2 <= x1 || y2 <= y1
      ) {
        ids.add(b.id)
      }
    }
    return ids
  }, [visibleBoxes, naturalSize])

  function removeOutOfBoundsBoxes() {
    if (outOfBoundsBoxIds.size === 0) return
    setBoxes(prev => prev.filter(b => !outOfBoundsBoxIds.has(b.id)))
    setSelectedBoxIds(prev => {
      const next = new Set(prev)
      for (const id of outOfBoundsBoxIds) next.delete(id)
      return next
    })
    if (activeBoxId && outOfBoundsBoxIds.has(activeBoxId)) setActiveBoxId(null)
    if (onDirtyChange) onDirtyChange(true)
  }

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

  useEffect(() => {
    if (onCharactersChange) {
      const chars = Object.keys(allAssignedCharCounts).filter(Boolean)
      onCharactersChange(chars)
    }
  }, [allAssignedCharCounts, onCharactersChange])

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
    if (!imgRef.current || !naturalSize || naturalSize.width <= 0) return 1
    const w = imgRef.current.clientWidth || displayWidth
    return w > 0 ? (w / naturalSize.width) : 1
  }

  function toNaturalCoords(clientX, clientY) {
    const targetEl = imgRef.current || containerRef.current
    const rect = targetEl.getBoundingClientRect()
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
          id: nextBoxId(),
          imageIndex: r.image_index ?? resolvedIndex,
          box: r.box,
          polygon: Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : null,
          characters: r.characters || [],
        }))
      } else if (Array.isArray(j.regions) && j.regions.length > 0) {
        detected = j.regions.map(r => ({
          id: nextBoxId(),
          imageIndex: resolvedIndex,
          box: r.box,
          polygon: Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : null,
          characters: [],
        }))
      } else {
        detected = (j.boxes || []).map(box => ({ id: nextBoxId(), imageIndex: resolvedIndex, box, polygon: null, characters: [] }))
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

  function commitPolygon(points) {
    const simplified = simplifyPolygon(points, 5)
    if (simplified.length < 3) return
    const [x1, y1, x2, y2] = boundingBoxOfPolygon(simplified)
    if (x2 - x1 < 8 || y2 - y1 < 8) return
    const id = nextBoxId()
    const initialChars = stampChar ? [stampChar] : []
    setBoxes(prev => [...prev, {
      id,
      imageIndex: currentImageIndex,
      box: [x1, y1, x2, y2],
      polygon: simplified.map(([px, py]) => [Math.round(px), Math.round(py)]),
      characters: initialChars,
    }])
    if (!stampChar) {
      setActiveBoxId(null)
      setSelectedBoxIds(new Set([id]))
    }
    setCharQuery('')
    if (onDirtyChange) onDirtyChange(true)
  }

  function handleMouseDown(e) {
    if (e.target !== containerRef.current && e.target !== imgRef.current) return
    const { x, y } = toNaturalCoords(e.clientX, e.clientY)
    if (drawMode === 'poly') {
      // If clicking near the start vertex with >= 3 points, close the polygon
      if (polyPoints.length >= 3) {
        const s = scale()
        const [sx, sy] = polyPoints[0]
        const dist = Math.hypot((x - sx) * s, (y - sy) * s)
        if (dist <= 12) {
          commitPolygon(polyPoints)
          setPolyPoints([])
          setPolyHoverPt(null)
          return
        }
      }
      setPolyPoints(prev => [...prev, [x, y]])
      return
    }
    if (drawMode === 'lasso') {
      drawStartRef.current = { x, y }
      setLassoPoints([[x, y]])
      return
    }
    drawStartRef.current = { x, y }
    setDrawRect({ x1: x, y1: y, x2: x, y2: y })
  }

  function handleMouseMove(e) {
    if (imgRef.current && naturalSize) {
      const { x, y } = toNaturalCoords(e.clientX, e.clientY)
      if (x >= 0 && y >= 0 && x < naturalSize.width && y < naturalSize.height) {
        const info = colorSamplerRef.current.sampleAt(imgRef.current, x, y)
        if (info) {
          setHoverColor(info)
          setCursorPos({ x: e.clientX, y: e.clientY })
        }
      } else {
        setCursorPos(null)
      }
      if (drawMode === 'poly' && polyPoints.length > 0) {
        setPolyHoverPt([x, y])
      }
    }
    if (!drawStartRef.current) return
    const { x, y } = toNaturalCoords(e.clientX, e.clientY)
    if (drawMode === 'lasso') {
      setLassoPoints(prev => {
        if (!prev || prev.length === 0) return [[x, y]]
        const last = prev[prev.length - 1]
        if (Math.hypot(x - last[0], y - last[1]) < 3) return prev
        return [...prev, [x, y]]
      })
      return
    }
    const start = drawStartRef.current
    setDrawRect({ x1: Math.min(start.x, x), y1: Math.min(start.y, y), x2: Math.max(start.x, x), y2: Math.max(start.y, y) })
  }

  function handleMouseUp() {
    if (drawMode === 'poly') return
    if (drawMode === 'lasso') {
      const pts = lassoPoints
      drawStartRef.current = null
      setLassoPoints(null)
      if (pts && pts.length >= 3) {
        commitPolygon(pts)
      }
      return
    }
    if (!drawStartRef.current || !drawRect) { drawStartRef.current = null; setDrawRect(null); return }
    const { x1, y1, x2, y2 } = drawRect
    drawStartRef.current = null
    setDrawRect(null)
    if (x2 - x1 < 8 || y2 - y1 < 8) return
    const id = nextBoxId()
    const initialChars = stampChar ? [stampChar] : []
    setBoxes(prev => [...prev, { id, imageIndex: currentImageIndex, box: [x1, y1, x2, y2], polygon: null, characters: initialChars }])
    if (!stampChar) {
      setActiveBoxId(null)
      setSelectedBoxIds(new Set([id]))
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
    setActiveBoxId(null)
    if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
      setSelectedBoxIds(prev => {
        const next = new Set(prev)
        if (next.has(boxId)) next.delete(boxId)
        else next.add(boxId)
        return next
      })
    } else {
      setSelectedBoxIds(new Set([boxId]))
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
    if (hoveredBoxId === boxId) setHoveredBoxId(null)
    setSelectedBoxIds(prev => {
      if (!prev.has(boxId)) return prev
      const next = new Set(prev)
      next.delete(boxId)
      return next
    })
    if (onDirtyChange) onDirtyChange(true)
  }

  function removeSelectedBoxes() {
    if (selectedBoxIds.size === 0) return
    setBoxes(prev => prev.filter(b => !selectedBoxIds.has(b.id)))
    if (activeBoxId && selectedBoxIds.has(activeBoxId)) setActiveBoxId(null)
    if (hoveredBoxId && selectedBoxIds.has(hoveredBoxId)) setHoveredBoxId(null)
    setSelectedBoxIds(new Set())
    if (onDirtyChange) onDirtyChange(true)
  }

  function labeledRegionsPayload() {
    return boxes.filter(b => b.characters.length > 0)
      .map(b => b.polygon && b.polygon.length >= 3
        ? { image_index: b.imageIndex, box: b.box, polygon: b.polygon, characters: b.characters }
        : { image_index: b.imageIndex, box: b.box, characters: b.characters }
      )
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
            <button key={img.id ?? img.index} className="btn" style={{ fontSize: 12 }}
              onClick={() => { setCurrentImageIndex(img.index); setSelectedBoxIds(new Set()) }}
              disabled={currentImageIndex === img.index}
            >
              {img.index + 1}枚目{currentImageIndex === img.index ? ' (表示中)' : ''}
              {labeledCountFor(img.index) > 0 && ` ✓${labeledCountFor(img.index)}`}
            </button>
          ))}
        </div>
      )}

      {outOfBoundsBoxIds.size > 0 && (
        <div style={{
          marginBottom: 10,
          background: 'rgba(239, 68, 68, 0.14)',
          border: '1px solid rgba(239, 68, 68, 0.5)',
          borderRadius: 6,
          padding: '8px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          flexWrap: 'wrap',
        }}>
          <span style={{ fontSize: 12, color: '#fca5a5' }}>
            ⚠ 画像の範囲外（見えていない領域）に {outOfBoundsBoxIds.size} 件の矩形があります。キャラ登録と同期するため削除してください。
          </span>
          <button
            type="button"
            className="btn"
            style={{ fontSize: 12, background: '#dc2626', color: '#fff', padding: '4px 10px' }}
            onClick={removeOutOfBoundsBoxes}
          >
            範囲外の矩形を削除
          </button>
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
        <div style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden', border: '1px solid #334155' }} title="領域の描き方を切り替えます">
          {[
            { id: 'box', label: '⬜ 矩形' },
            { id: 'lasso', label: '✏️ 投げ縄' },
            { id: 'poly', label: '📐 多角形' },
          ].map(m => (
            <button
              key={m.id}
              type="button"
              onClick={() => {
                setDrawMode(m.id)
                setDrawRect(null)
                setLassoPoints(null)
                setPolyPoints([])
                setPolyHoverPt(null)
              }}
              style={{
                border: 'none',
                padding: '5px 9px',
                fontSize: 12,
                cursor: 'pointer',
                background: drawMode === m.id ? '#2563eb' : '#0f172a',
                color: drawMode === m.id ? '#fff' : '#cbd5e1',
                fontWeight: drawMode === m.id ? 600 : 400,
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
        {drawMode === 'poly' && polyPoints.length > 0 && (
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: 11, color: '#93c5fd' }}>頂点 {polyPoints.length}点</span>
            {polyPoints.length >= 3 && (
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: '3px 8px', background: '#16a34a', color: '#fff' }}
                onClick={() => { commitPolygon(polyPoints); setPolyPoints([]); setPolyHoverPt(null) }}
              >
                ✓ 閉じる (Enter)
              </button>
            )}
            <button
              type="button"
              className="btn"
              style={{ fontSize: 11, padding: '3px 8px', background: '#334155', color: '#cbd5e1' }}
              onClick={() => { setPolyPoints([]); setPolyHoverPt(null) }}
            >
              ✕ 取消 (Esc)
            </button>
          </div>
        )}
        <button
          type="button"
          className="btn"
          onClick={() => setLoupeEnabled(v => !v)}
          style={{
            fontSize: 12,
            background: loupeEnabled ? '#1e3a8a' : undefined,
            color: loupeEnabled ? '#bfdbfe' : undefined,
          }}
          title="画像上にカーソルを合わせた時に隣へ拡大ルーペと色名を表示します"
        >
          🎨 色ルーペ: {loupeEnabled ? 'ON' : 'OFF'}
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

      {!compact && (
        <div style={{ marginBottom: 10, fontSize: 12, color: '#94a3b8' }}>
          画像上をドラッグすると手動で矩形を追加できます。矩形をクリックしてキャラ名を割り当ててください(1画像内に同じキャラが何回登場してもそれぞれ指定できます)。
          Shift/Ctrl+クリックで複数選択、または「📌 スタンプ割当」で同じキャラを連続クリックして一括入力できます。
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div
        ref={containerRef}
        style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', cursor: stampChar ? 'copy' : 'crosshair', userSelect: 'none' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onDoubleClick={() => {
          if (drawMode === 'poly' && polyPoints.length >= 3) {
            commitPolygon(polyPoints)
            setPolyPoints([])
            setPolyHoverPt(null)
          }
        }}
        onMouseLeave={() => { setHoverColor(null); setCursorPos(null); setPolyHoverPt(null) }}
      >
        <img
          ref={imgRef}
          src={previewUrl}
          alt={`item-${item.id}`}
          draggable={false}
          style={{
            maxWidth: '100%',
            maxHeight: compact ? '46vh' : undefined,
            width: compact ? 'auto' : undefined,
            display: 'block',
          }}
          onLoad={e => {
            setNaturalSize({ width: e.target.naturalWidth, height: e.target.naturalHeight })
            if (e.target.clientWidth > 0) setDisplayWidth(e.target.clientWidth)
          }}
        />

        {naturalSize && (
          <svg
            viewBox={`0 0 ${naturalSize.width} ${naturalSize.height}`}
            style={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              pointerEvents: 'none',
              zIndex: 4,
            }}
          >
            {sortedVisibleBoxes.filter(b => Array.isArray(b.polygon) && b.polygon.length >= 3).map(b => {
              const isLabeled = b.characters.length > 0
              const isSelected = selectedBoxIds.has(b.id)
              const isHovered = hoveredBoxId === b.id
              const firstChar = b.characters[0]
              const charColor = isLabeled ? getCharColor(firstChar) : null
              const borderColor = isSelected
                ? '#3b82f6'
                : (isHovered ? '#facc15' : (charColor ? charColor.border : (isLabeled ? '#22c55e' : '#f59e0b')))
              const bgColor = isSelected
                ? 'rgba(59,130,246,0.24)'
                : (charColor ? charColor.bg : (isLabeled ? 'rgba(34,197,94,0.12)' : 'rgba(245,158,11,0.12)'))
              const ptsAttr = b.polygon.map(([px, py]) => `${px},${py}`).join(' ')
              const s = scale() || 1
              const strokeW = (isSelected || isHovered ? 3 : 2) / s
              return (
                <polygon
                  key={`poly-${b.id}`}
                  points={ptsAttr}
                  fill={bgColor}
                  stroke={borderColor}
                  strokeWidth={strokeW}
                  strokeLinejoin="round"
                  style={{ pointerEvents: 'auto', cursor: stampChar ? 'copy' : 'pointer' }}
                  onMouseDown={ev => ev.stopPropagation()}
                  onClick={ev => handleBoxClick(b.id, ev)}
                />
              )
            })}
            {lassoPoints && lassoPoints.length >= 2 && (() => {
              const s = scale() || 1
              const ptsAttr = lassoPoints.map(([px, py]) => `${px},${py}`).join(' ')
              return (
                <>
                  <polygon
                    points={ptsAttr}
                    fill="rgba(96, 165, 250, 0.16)"
                    stroke="#60a5fa"
                    strokeWidth={2 / s}
                    strokeDasharray={`${5 / s} ${3 / s}`}
                    strokeLinejoin="round"
                  />
                </>
              )
            })()}
            {polyPoints.length > 0 && (() => {
              const s = scale() || 1
              const previewPts = polyHoverPt ? [...polyPoints, polyHoverPt] : polyPoints
              const ptsAttr = previewPts.map(([px, py]) => `${px},${py}`).join(' ')
              return (
                <>
                  {previewPts.length >= 2 && (
                    <polyline
                      points={ptsAttr}
                      fill="none"
                      stroke="#60a5fa"
                      strokeWidth={2 / s}
                      strokeDasharray={`${5 / s} ${3 / s}`}
                    />
                  )}
                  {polyPoints.map(([px, py], i) => (
                    <circle
                      key={i}
                      cx={px}
                      cy={py}
                      r={(i === 0 ? 6 : 4) / s}
                      fill={i === 0 && polyPoints.length >= 3 ? '#22c55e' : '#60a5fa'}
                      stroke="#0f172a"
                      strokeWidth={1.5 / s}
                    />
                  ))}
                </>
              )
            })()}
          </svg>
        )}

        {naturalSize && sortedVisibleBoxes.map((b, idx) => {
          const s = scale()
          const [x1, y1, x2, y2] = b.box
          const hasPoly = Array.isArray(b.polygon) && b.polygon.length >= 3
          const isLabeled = b.characters.length > 0
          const isSelected = selectedBoxIds.has(b.id)
          const isActive = activeBoxId === b.id
          const isHovered = hoveredBoxId === b.id
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

          // z-index: Base stacking order puts smaller boxes (higher idx) on top of larger boxes (lower idx).
          // Selected boxes, hovered box from the list, and active box get higher priority.
          const zIndex = isActive ? 100 : (isHovered ? 90 : (isSelected ? 50 + idx : 1 + idx))

          const labelText = b.characters.length > 0
            ? b.characters.map(c => {
                const count = currentCharCounts[c] || 0
                return count > 1 && occurrenceIdx > 0 ? `${c} #${occurrenceIdx}` : c
              }).join('、')
            : '?'

          return (
            <div key={b.id}
              onClick={hasPoly ? undefined : ev => handleBoxClick(b.id, ev)}
              onMouseDown={hasPoly ? undefined : ev => ev.stopPropagation()}
              style={{
                position: 'absolute', left: x1 * s, top: y1 * s, width: (x2 - x1) * s, height: (y2 - y1) * s,
                border: hasPoly ? 'none' : `2px solid ${borderColor}`,
                outline: hasPoly
                  ? (isSelected ? '1px dashed #3b82f6' : (isHovered ? '1px dashed #facc15' : 'none'))
                  : (isSelected ? '3px solid #3b82f6' : (isHovered ? '3px solid #facc15' : (stampChar ? '1px dashed #60a5fa' : 'none'))),
                outlineOffset: 2,
                background: hasPoly ? 'transparent' : bgColor,
                boxShadow: !hasPoly && isHovered ? '0 0 0 2px rgba(250, 204, 21, 0.45)' : undefined,
                cursor: hasPoly ? 'default' : (stampChar ? 'copy' : 'pointer'),
                pointerEvents: hasPoly ? 'none' : 'auto',
                boxSizing: 'border-box',
                transition: 'border-color 0.1s ease, background 0.1s ease',
                zIndex: hasPoly ? Math.max(zIndex, 10) : zIndex,
              }}
              title={!hasPoly && stampChar ? `クリックして「${stampChar}」を割り当て/解除` : undefined}
            >
              <span
                onClick={hasPoly ? ev => handleBoxClick(b.id, ev) : undefined}
                onMouseDown={hasPoly ? ev => ev.stopPropagation() : undefined}
                style={{
                  position: 'absolute', top: -20, left: 0, fontSize: 11, padding: '1px 5px', borderRadius: 3,
                  background: tagBg, color: '#fff', whiteSpace: 'nowrap',
                  maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis',
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  pointerEvents: 'auto',
                  cursor: hasPoly ? (stampChar ? 'copy' : 'pointer') : undefined,
                }}
              >
                <span>{labelText}</span>
                {totalSameChar > 1 && (
                  <span style={{ fontSize: 9, opacity: 0.8, background: 'rgba(0,0,0,0.3)', padding: '0 3px', borderRadius: 2 }}>
                    計{totalSameChar}
                  </span>
                )}
              </span>
              <button
                onClick={ev => { ev.stopPropagation(); removeBox(b.id) }}
                onMouseDown={ev => ev.stopPropagation()}
                style={{
                  position: 'absolute', top: -20, right: 0, fontSize: 11, border: 'none', borderRadius: 3,
                  background: '#7f1d1d', color: '#fff', padding: '1px 5px', cursor: 'pointer', lineHeight: 1,
                  pointerEvents: 'auto',
                }}
                title="この領域を削除"
              >×</button>
            </div>
          )
        })}

        {drawRect && naturalSize && (() => {
          const s = scale()
          return (
            <div style={{
              position: 'absolute', left: drawRect.x1 * s, top: drawRect.y1 * s,
              width: (drawRect.x2 - drawRect.x1) * s, height: (drawRect.y2 - drawRect.y1) * s,
              border: '2px dashed #60a5fa', pointerEvents: 'none', zIndex: 200,
            }} />
          )
        })()}
      </div>

      {/* Right-side column: character assignment panel + region list */}
      <div
        onClick={ev => ev.stopPropagation()}
        onMouseDown={ev => ev.stopPropagation()}
        style={{
          flex: '1 1 300px',
          minWidth: 260,
          maxWidth: 480,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
      <div
        style={{
          background: '#1e293b',
          border: '1px solid #334155',
          borderRadius: 6,
          padding: 10,
        }}
      >
        {selectedBoxIds.size > 0 ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, color: '#f1f5f9', fontWeight: 600 }}>
                {selectedBoxIds.size}件の矩形を選択中
              </span>
              <button
                type="button"
                onClick={removeSelectedBoxes}
                style={{ marginLeft: 'auto', fontSize: 11, color: '#fecaca', background: '#7f1d1d', border: 'none', borderRadius: 4, padding: '3px 8px', cursor: 'pointer' }}
                title="選択中の矩形をまとめて削除します"
              >選択中を削除</button>
              <button
                type="button"
                onClick={() => setSelectedBoxIds(new Set())}
                style={{ fontSize: 11, color: '#94a3b8', background: 'none', border: 'none', cursor: 'pointer' }}
              >選択解除</button>
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 8 }}>
              選んだキャラ名を全てに割り当てます（緑色=割り当て済み、もう一度クリックで解除）
            </div>

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
                  <div key={c} style={{ display: 'inline-flex', alignItems: 'center', borderRadius: 4, background: assigned ? '#166534' : '#334155' }}>
                    <button
                      type="button"
                      onClick={() => toggleCharacterForSelectedBoxes(c)}
                      style={{
                        fontSize: 12, padding: '5px 8px', borderRadius: '4px 0 0 4px', border: 'none', cursor: 'pointer',
                        background: 'transparent', color: assigned ? '#dcfce7' : '#f1f5f9',
                      }}
                    >
                      {assigned ? '✓ ' : ''}{c}
                      {allAssignedCharCounts[c] > 0 && (
                        <span style={{ fontSize: 10, opacity: 0.75, marginLeft: 4 }}>({allAssignedCharCounts[c]})</span>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => setStampChar(c)}
                      style={{ border: 'none', background: 'transparent', color: '#93c5fd', cursor: 'pointer', padding: '5px 6px 5px 2px', fontSize: 11 }}
                      title={`「${c}」の連続割り当てスタンプモードにする`}
                    >
                      📌
                    </button>
                  </div>
                )
              })}
              {charQuery.trim() && !charSuggestions.some(c => c.toLowerCase() === charQuery.trim().toLowerCase()) && (
                <button
                  type="button"
                  onClick={() => toggleCharacterForSelectedBoxes(charQuery)}
                  style={{ fontSize: 12, padding: '5px 10px', borderRadius: 4, border: 'none', background: '#1e3a8a', color: '#93c5fd', cursor: 'pointer' }}
                >＋「{charQuery.trim()}」を新規追加</button>
              )}
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
            <div style={{ fontWeight: 600, color: '#cbd5e1', marginBottom: 4 }}>キャラ割り当て</div>
            画像上の領域、または下の領域一覧から選択するとここでキャラを割り当てられます（Shift/Ctrl+クリックで複数選択）。
          </div>
        )}
      </div>

      {visibleBoxes.length > 0 && (
        <div style={{
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: 6,
          padding: '8px 10px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>
              領域一覧 ({visibleBoxes.length}件)
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: compact ? 160 : 220, overflowY: 'auto' }}>
            {visibleBoxes.map((b, idx) => {
              const [x1, y1, x2, y2] = b.box
              const w = Math.max(0, Math.round(x2 - x1))
              const h = Math.max(0, Math.round(y2 - y1))
              const isLabeled = b.characters.length > 0
              const isSelected = selectedBoxIds.has(b.id)
              const isHovered = hoveredBoxId === b.id
              const isOob = outOfBoundsBoxIds.has(b.id)
              const firstChar = b.characters[0]
              const charColor = isLabeled ? getCharColor(firstChar) : null
              const badgeColor = charColor ? charColor.border : (isLabeled ? '#22c55e' : '#f59e0b')
              return (
                <div
                  key={b.id}
                  onMouseEnter={() => setHoveredBoxId(b.id)}
                  onMouseLeave={() => setHoveredBoxId(prev => (prev === b.id ? null : prev))}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '4px 8px',
                    borderRadius: 4,
                    background: isSelected ? 'rgba(59, 130, 246, 0.18)' : (isHovered ? '#1e293b' : '#111827'),
                    border: isSelected ? '1px solid #3b82f6' : (isHovered ? '1px solid #facc15' : '1px solid #1e293b'),
                    fontSize: 12,
                  }}
                >
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      background: badgeColor,
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ color: '#94a3b8', fontVariantNumeric: 'tabular-nums', minWidth: 24 }}>
                    #{idx + 1}
                  </span>
                  <span style={{
                    color: isLabeled ? '#f1f5f9' : '#fbbf24',
                    fontWeight: isLabeled ? 500 : 400,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    flex: 1,
                  }}>
                    {isLabeled ? b.characters.join('、') : '未割当 (?)'}
                  </span>
                  {isOob && (
                    <span style={{ fontSize: 10, color: '#fca5a5', background: 'rgba(239,68,68,0.2)', padding: '1px 5px', borderRadius: 3 }}>
                      範囲外
                    </span>
                  )}
                  <span style={{ fontSize: 11, color: '#64748b', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                    ({Math.round(x1)}, {Math.round(y1)}) {w}×{h}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedBoxIds(prev => {
                        const next = new Set(prev)
                        if (next.has(b.id)) next.delete(b.id)
                        else next.add(b.id)
                        return next
                      })
                    }}
                    style={{
                      fontSize: 11,
                      padding: '2px 7px',
                      borderRadius: 3,
                      border: '1px solid #475569',
                      background: isSelected ? '#2563eb' : '#1e293b',
                      color: '#f1f5f9',
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                    title="この矩形を選択してキャラを割り当て/解除します"
                  >
                    {isSelected ? '選択中' : '選択'}
                  </button>
                  <button
                    type="button"
                    onClick={() => removeBox(b.id)}
                    style={{
                      fontSize: 11,
                      padding: '2px 7px',
                      borderRadius: 3,
                      border: 'none',
                      background: '#7f1d1d',
                      color: '#fff',
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                    title="この矩形を削除"
                  >
                    削除
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}
      </div>
      </div>

      {loupeEnabled && !drawRect && (
        <ColorInspectLoupe
          imgRef={imgRef}
          sampler={colorSamplerRef.current}
          sample={hoverColor}
          cursorPos={cursorPos}
        />
      )}

      <div style={{ marginTop: compact ? 6 : 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
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
