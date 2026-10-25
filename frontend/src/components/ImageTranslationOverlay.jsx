import React, { useState } from 'react'

/**
 * Overlay component rendered directly over the preview image.
 * Displays bounding boxes for detected foreign text and shows translated
 * Japanese text cards in adjacent space.
 */
export default function ImageTranslationOverlay({ translations = [], visible = true }) {
  const [hoveredIdx, setHoveredIdx] = useState(null)

  if (!visible || !translations || translations.length === 0) {
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
        overflow: 'hidden',
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

        // Determine tooltip placement:
        // If box is in top 40% of image, place tooltip below the box; else above the box.
        const placeBelow = top < 40

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
                  : '1.5px dashed rgba(59, 130, 246, 0.75)',
                background: isHovered
                  ? 'rgba(59, 130, 246, 0.25)'
                  : 'rgba(59, 130, 246, 0.12)',
                borderRadius: 4,
                pointerEvents: 'auto',
                cursor: 'pointer',
                transition: 'all 0.15s ease-in-out',
                boxSizing: 'border-box',
              }}
              title={`${item.original_text} → ${item.translated_text}`}
            />

            {/* Translation card / Speech bubble */}
            <div
              onMouseEnter={() => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
              style={{
                position: 'absolute',
                left: `${Math.min(85, Math.max(2, left))}%`,
                top: placeBelow
                  ? `${Math.min(92, top + height + 1)}%`
                  : undefined,
                bottom: !placeBelow
                  ? `${Math.min(92, 100 - top + 1)}%`
                  : undefined,
                maxWidth: '45%',
                minWidth: 120,
                background: isHovered ? 'rgba(15, 23, 42, 0.98)' : 'rgba(15, 23, 42, 0.90)',
                border: isHovered ? '1.5px solid #60a5fa' : '1px solid rgba(59, 130, 246, 0.6)',
                borderRadius: 8,
                padding: '6px 10px',
                color: '#f8fafc',
                fontSize: 13,
                lineHeight: 1.45,
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.65)',
                backdropFilter: 'blur(4px)',
                pointerEvents: 'auto',
                zIndex: isHovered ? 20 : 12,
                transform: isHovered ? 'scale(1.02)' : 'none',
                transition: 'transform 0.15s ease, border-color 0.15s ease, background 0.15s ease',
                wordBreak: 'break-word',
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
              <div style={{ fontWeight: 500, color: '#f1f5f9' }}>
                {item.translated_text}
              </div>
            </div>
          </React.Fragment>
        )
      })}
    </div>
  )
}

