import React, { useState } from 'react'

/**
 * Overlay component rendered directly over the preview image.
 * Displays bounding boxes for detected foreign text and shows translated
 * Japanese text cards in adjacent space.
 *
 * Mode can be:
 * - 'always': all translation cards and bounding boxes are visible
 * - 'hover': bounding boxes are subtle; translation card only appears when hovering over the box or card
 * - 'hidden': overlay is completely hidden
 */
export default function ImageTranslationOverlay({
  translations = [],
  mode = 'always', // 'always' | 'hover' | 'hidden'
  visible = true,
}) {
  const [hoveredIdx, setHoveredIdx] = useState(null)

  const effectiveMode = (!visible || mode === 'hidden')
    ? 'hidden'
    : (mode === 'hover' ? 'hover' : 'always')

  if (effectiveMode === 'hidden' || !translations || translations.length === 0) {
    return null
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 10,
        overflow: 'visible', // Allow speech bubbles to expand naturally into window margins
      }}
    >
      {translations.map((item, idx) => {
        const box = item.box_2d || [0, 0, 0, 0]
        const [ymin, xmin, ymax, xmax] = box

        const top = ymin / 10
        const left = xmin / 10
        const width = Math.max(2, (xmax - xmin) / 10)
        const height = Math.max(2, (ymax - ymin) / 10)

        const isHovered = hoveredIdx === idx
        const showCard = effectiveMode === 'always' || (effectiveMode === 'hover' && isHovered)

        // Determine tooltip vertical placement:
        // If box is in top 40% of image, place tooltip below the box; else above the box.
        const placeBelow = top < 40

        // Determine horizontal anchoring:
        // If box is in the right half (> 60%), anchor the card to the right side of the bubble
        // so it expands leftward or smoothly extends into adjacent window space without being cut off.
        const isRightHalf = left > 60

        return (
          <React.Fragment key={idx}>
            {/* Bounding box over original text */}
            <div
              onMouseEnter={() => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
              style={{
                position: 'absolute',
                top: `${top}%`,
                left: `${left}%`,
                width: `${width}%`,
                height: `${height}%`,
                border: isHovered
                  ? '2px solid #60a5fa'
                  : (effectiveMode === 'hover'
                      ? '1.5px dashed rgba(59, 130, 246, 0.45)'
                      : '1.5px dashed rgba(59, 130, 246, 0.75)'),
                background: isHovered
                  ? 'rgba(59, 130, 246, 0.25)'
                  : (effectiveMode === 'hover'
                      ? 'rgba(59, 130, 246, 0.05)'
                      : 'rgba(59, 130, 246, 0.12)'),
                borderRadius: 4,
                pointerEvents: 'auto',
                cursor: 'pointer',
                transition: 'all 0.15s ease-in-out',
                boxSizing: 'border-box',
              }}
              title={`${item.original_text} → ${item.translated_text}`}
            />

            {/* Translation card / Speech bubble */}
            {showCard && (
              <div
                onMouseEnter={() => setHoveredIdx(idx)}
                onMouseLeave={() => setHoveredIdx(null)}
                style={{
                  position: 'absolute',
                  left: isRightHalf ? undefined : `${Math.max(0, left)}%`,
                  right: isRightHalf ? `${Math.max(-5, 100 - (left + width))}%` : undefined,
                  top: placeBelow
                    ? `${Math.min(92, top + height + 1)}%`
                    : undefined,
                  bottom: !placeBelow
                    ? `${Math.min(92, 100 - top + 1)}%`
                    : undefined,
                  maxWidth: 'min(380px, 45vw)',
                  minWidth: 130,
                  maxHeight: '40vh',
                  overflowY: 'auto',
                  background: isHovered ? 'rgba(15, 23, 42, 0.98)' : 'rgba(15, 23, 42, 0.92)',
                  border: isHovered ? '1.5px solid #60a5fa' : '1px solid rgba(59, 130, 246, 0.65)',
                  borderRadius: 8,
                  padding: '6px 10px',
                  color: '#f8fafc',
                  fontSize: 13,
                  lineHeight: 1.45,
                  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.75)',
                  backdropFilter: 'blur(6px)',
                  pointerEvents: 'auto',
                  zIndex: isHovered ? 25 : 15,
                  transform: isHovered ? 'scale(1.02)' : 'none',
                  transition: 'transform 0.15s ease, border-color 0.15s ease, background 0.15s ease',
                  wordBreak: 'break-word',
                  animation: 'fadeIn 0.12s ease-out',
                }}
              >
                {item.original_text && (
                  <div
                    style={{
                      fontSize: 11,
                      color: '#94a3b8',
                      marginBottom: 3,
                      borderBottom: '1px solid rgba(148, 163, 184, 0.2)',
                      paddingBottom: 2,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                    }}
                  >
                    <span
                      style={{
                        background: 'rgba(59, 130, 246, 0.25)',
                        color: '#93c5fd',
                        padding: '1px 4px',
                        borderRadius: 3,
                        fontSize: 10,
                        fontWeight: 600,
                      }}
                    >
                      {(item.source_lang || 'RAW').toUpperCase()}
                    </span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {item.original_text}
                    </span>
                  </div>
                )}
                <div style={{ fontWeight: 500, color: '#f1f5f9', whiteSpace: 'pre-wrap' }}>
                  {item.translated_text}
                </div>
              </div>
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}
