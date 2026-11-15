import React, { useState, useRef, useEffect } from 'react'
import { createImagePixelSampler } from '../lib/colorInspect'
import ColorInspectLoupe from './ColorInspectLoupe'

export default function ImageColorViewer({ src }) {
  const imgRef = useRef(null)
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

      {loupeEnabled && !locked && (
        <ColorInspectLoupe
          imgRef={imgRef}
          sampler={samplerRef.current}
          sample={sample}
          cursorPos={cursorPos}
        />
      )}
    </div>
  )
}
