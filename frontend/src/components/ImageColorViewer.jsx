import React, { useState, useRef, useEffect } from 'react'
import { createImagePixelSampler } from '../lib/colorInspect'

const LOUPE_SIZE = 108
const LOUPE_SAMPLE_RADIUS = 9 // 19x19 source pixels zoomed into 108x108

export default function ImageColorViewer({ src }) {
  const imgRef = useRef(null)
  const loupeCanvasRef = useRef(null)
  const samplerRef = useRef(null)
  if (!samplerRef.current) samplerRef.current = createImagePixelSampler()

  const [sample, setSample] = useState(null)
  const [locked, setLocked] = useState(false)
  const [loupeEnabled, setLoupeEnabled] = useState(true)
  const [cursorPos, setCursorPos] = useState(null)
  const [copiedHex, setCopiedHex] = useState(false)
  const [imgError, setImgError] = useState(false)

  useEffect(() => {
    document.title = 'Image Color Viewer'
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape' && locked) {
        setLocked(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [locked])

  function sampleFromPointer(clientX, clientY) {
    const img = imgRef.current
    if (!img || !img.complete || !img.naturalWidth) return null
    const rect = img.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
      return null
    }
    const nx = ((clientX - rect.left) / rect.width) * img.naturalWidth
    const ny = ((clientY - rect.top) / rect.height) * img.naturalHeight
    return samplerRef.current.sampleAt(img, nx, ny)
  }

  function drawLoupe(info) {
    const loupe = loupeCanvasRef.current
    const img = imgRef.current
    if (!loupe || !img || !info) return
    const srcCanvas = samplerRef.current.getCanvas(img)
    if (!srcCanvas) return
    const ctx = loupe.getContext('2d')
    if (!ctx) return

    const span = LOUPE_SAMPLE_RADIUS * 2 + 1
    ctx.imageSmoothingEnabled = false
    ctx.fillStyle = '#0f172a'
    ctx.fillRect(0, 0, LOUPE_SIZE, LOUPE_SIZE)

    const sx = info.x - LOUPE_SAMPLE_RADIUS
    const sy = info.y - LOUPE_SAMPLE_RADIUS
    ctx.drawImage(srcCanvas, sx, sy, span, span, 0, 0, LOUPE_SIZE, LOUPE_SIZE)

    // Center pixel highlight
    const cell = LOUPE_SIZE / span
    const cx = LOUPE_SAMPLE_RADIUS * cell
    const cy = LOUPE_SAMPLE_RADIUS * cell
    ctx.strokeStyle = 'rgba(255,255,255,0.95)'
    ctx.lineWidth = 1.5
    ctx.strokeRect(cx, cy, cell, cell)
    ctx.strokeStyle = 'rgba(0,0,0,0.8)'
    ctx.lineWidth = 1
    ctx.strokeRect(cx - 1, cy - 1, cell + 2, cell + 2)
  }

  useEffect(() => {
    if (loupeEnabled && cursorPos && sample) {
      drawLoupe(sample)
    }
  }, [loupeEnabled, cursorPos, sample])

  function handleMouseMove(e) {
    if (locked) return
    const info = sampleFromPointer(e.clientX, e.clientY)
    if (!info) {
      setCursorPos(null)
      return
    }
    setSample(info)
    setCursorPos({ x: e.clientX, y: e.clientY })
  }

  function handleMouseLeave() {
    setCursorPos(null)
    if (!locked) setSample(null)
  }

  function handleClick(e) {
    if (locked) {
      setLocked(false)
      const info = sampleFromPointer(e.clientX, e.clientY)
      if (info) {
        setSample(info)
        setCursorPos({ x: e.clientX, y: e.clientY })
      }
      return
    }
    const info = sampleFromPointer(e.clientX, e.clientY)
    if (info) {
      setSample(info)
      setLocked(true)
    }
  }

  async function copyHex() {
    if (!sample) return
    try {
      await navigator.clipboard.writeText(sample.hex)
      setCopiedHex(true)
      setTimeout(() => setCopiedHex(false), 1200)
    } catch (_) {}
  }

  const loupeLeft = cursorPos
    ? (cursorPos.x + 20 + LOUPE_SIZE > window.innerWidth ? cursorPos.x - LOUPE_SIZE - 16 : cursorPos.x + 20)
    : 0
  const loupeTop = cursorPos
    ? (cursorPos.y + 20 + LOUPE_SIZE > window.innerHeight ? cursorPos.y - LOUPE_SIZE - 16 : cursorPos.y + 20)
    : 0

  return (
    <div style={{
      minHeight: '100vh',
      background: '#0e0e10',
      color: '#f1f5f9',
      display: 'flex',
      flexDirection: 'column',
      fontFamily: 'system-ui, -apple-system, sans-serif',
    }}>
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 20,
        background: 'rgba(15, 23, 42, 0.95)',
        borderBottom: '1px solid #1e293b',
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {sample ? (
            <>
              <span
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 6,
                  background: sample.hex,
                  border: '2px solid rgba(255,255,255,0.8)',
                  boxShadow: '0 1px 4px rgba(0,0,0,0.5)',
                  flexShrink: 0,
                }}
              />
              <span style={{ fontSize: 17, fontWeight: 700, color: '#ffffff' }}>
                {sample.label}
              </span>
              <button
                type="button"
                onClick={copyHex}
                title="クリックしてカラーコードをコピー"
                style={{
                  background: '#1e293b',
                  color: '#93c5fd',
                  border: '1px solid #334155',
                  borderRadius: 6,
                  padding: '4px 10px',
                  fontSize: 13,
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  cursor: 'pointer',
                }}
              >
                {copiedHex ? `コピー済み: ${sample.hex}` : sample.hex}
              </button>
              <span style={{ fontSize: 12, color: '#94a3b8', fontFamily: 'ui-monospace, monospace' }}>
                {sample.rgb} / HSV({sample.hsv.h}°, {sample.hsv.s}%, {sample.hsv.v}%) / ({sample.x}, {sample.y})
              </span>
              {locked && (
                <button
                  type="button"
                  onClick={() => setLocked(false)}
                  style={{
                    background: '#2563eb',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 6,
                    padding: '4px 10px',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  ピン留め中（解除）
                </button>
              )}
            </>
          ) : (
            <span style={{ fontSize: 13, color: '#94a3b8' }}>
              画像上にカーソルを合わせるとカラーコードと色名を検出します（クリックで色固定）
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            onClick={() => setLoupeEnabled(v => !v)}
            style={{
              background: loupeEnabled ? '#1d4ed8' : '#1e293b',
              color: '#f1f5f9',
              border: '1px solid #334155',
              borderRadius: 6,
              padding: '5px 10px',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            拡大ルーペ: {loupeEnabled ? 'ON' : 'OFF'}
          </button>
          <a
            href={src}
            style={{
              color: '#93c5fd',
              fontSize: 12,
              textDecoration: 'none',
              padding: '5px 10px',
              border: '1px solid #334155',
              borderRadius: 6,
              background: '#1e293b',
            }}
          >
            元画像のみ開く
          </a>
        </div>
      </div>

      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
        overflow: 'auto',
      }}>
        {imgError ? (
          <div style={{ color: '#f87171', fontSize: 14 }}>画像の読み込みに失敗しました。</div>
        ) : (
          <img
            ref={imgRef}
            src={src}
            alt=""
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
            onClick={handleClick}
            onError={() => setImgError(true)}
            style={{
              maxWidth: '100%',
              maxHeight: 'calc(100vh - 88px)',
              objectFit: 'contain',
              cursor: 'crosshair',
              display: 'block',
              userSelect: 'none',
            }}
          />
        )}
      </div>

      {loupeEnabled && !locked && cursorPos && sample && (
        <div style={{
          position: 'fixed',
          left: loupeLeft,
          top: loupeTop,
          pointerEvents: 'none',
          zIndex: 30,
          borderRadius: 8,
          overflow: 'hidden',
          border: '2px solid rgba(255,255,255,0.85)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
          background: '#0f172a',
        }}>
          <canvas
            ref={loupeCanvasRef}
            width={LOUPE_SIZE}
            height={LOUPE_SIZE}
            style={{ display: 'block' }}
          />
          <div style={{
            padding: '3px 6px',
            fontSize: 11,
            background: 'rgba(15, 23, 42, 0.92)',
            color: '#f8fafc',
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            whiteSpace: 'nowrap',
          }}>
            <span style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: sample.hex,
              border: '1px solid #fff',
              flexShrink: 0,
            }} />
            <span>{sample.label}</span>
          </div>
        </div>
      )}
    </div>
  )
}
