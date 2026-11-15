import React, { useRef, useEffect } from 'react'

const LOUPE_SAMPLE_RADIUS = 9 // 19x19 source pixels

export default function ColorInspectLoupe({
  imgRef,
  sampler,
  sample,
  cursorPos,
  size = 112,
  showHex = true,
}) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const loupe = canvasRef.current
    const img = imgRef && imgRef.current
    if (!loupe || !img || !sampler || !sample || !cursorPos) return
    const srcCanvas = sampler.getCanvas(img)
    if (!srcCanvas) return
    const ctx = loupe.getContext('2d')
    if (!ctx) return

    const span = LOUPE_SAMPLE_RADIUS * 2 + 1
    ctx.imageSmoothingEnabled = false
    ctx.fillStyle = '#0f172a'
    ctx.fillRect(0, 0, size, size)

    const sx = sample.x - LOUPE_SAMPLE_RADIUS
    const sy = sample.y - LOUPE_SAMPLE_RADIUS
    ctx.drawImage(srcCanvas, sx, sy, span, span, 0, 0, size, size)

    const cell = size / span
    const cx = LOUPE_SAMPLE_RADIUS * cell
    const cy = LOUPE_SAMPLE_RADIUS * cell
    ctx.strokeStyle = 'rgba(255,255,255,0.95)'
    ctx.lineWidth = 1.5
    ctx.strokeRect(cx, cy, cell, cell)
    ctx.strokeStyle = 'rgba(0,0,0,0.8)'
    ctx.lineWidth = 1
    ctx.strokeRect(cx - 1, cy - 1, cell + 2, cell + 2)
  }, [imgRef, sampler, sample, cursorPos, size])

  if (!cursorPos || !sample) return null

  const estWidth = Math.max(size, 150)
  const estHeight = size + (showHex ? 42 : 26)
  const left = cursorPos.x + 20 + estWidth > window.innerWidth
    ? Math.max(8, cursorPos.x - estWidth - 14)
    : cursorPos.x + 20
  const top = cursorPos.y + 20 + estHeight > window.innerHeight
    ? Math.max(8, cursorPos.y - estHeight - 14)
    : cursorPos.y + 20

  return (
    <div style={{
      position: 'fixed',
      left,
      top,
      pointerEvents: 'none',
      zIndex: 9999,
      borderRadius: 8,
      overflow: 'hidden',
      border: '2px solid rgba(255,255,255,0.85)',
      boxShadow: '0 6px 20px rgba(0,0,0,0.65)',
      background: 'rgba(15, 23, 42, 0.95)',
      minWidth: size,
    }}>
      <canvas
        ref={canvasRef}
        width={size}
        height={size}
        style={{ display: 'block', margin: '0 auto' }}
      />
      <div style={{
        padding: '4px 8px',
        fontSize: 12,
        color: '#f8fafc',
        borderTop: '1px solid rgba(255,255,255,0.12)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <span style={{
            width: 12,
            height: 12,
            borderRadius: 3,
            background: sample.hex,
            border: '1px solid rgba(255,255,255,0.85)',
            flexShrink: 0,
          }} />
          <strong style={{ fontWeight: 700 }}>{sample.label}</strong>
        </div>
        {showHex && (
          <div style={{
            marginTop: 2,
            fontSize: 11,
            color: '#93c5fd',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            whiteSpace: 'nowrap',
          }}>
            {sample.hex} ({sample.x}, {sample.y})
          </div>
        )}
      </div>
    </div>
  )
}
