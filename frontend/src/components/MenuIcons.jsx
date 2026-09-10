import React from 'react'

// Small monochrome icon set for App.jsx's header menu (see MenuIconLabel),
// matching this app's existing line-icon convention (viewBox 0 0 24 24,
// stroke=currentColor so each icon just inherits the menu item's own text
// color instead of carrying a fixed one — same approach ScrollList/
// PreviewPane's own inline SVG buttons already use).
const STROKE = { fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }

function Svg({ children, size = 18 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" {...STROKE} style={{ display: 'block' }}>{children}</svg>
}

// A small "queue/list" glyph (three stacked bars) used as a corner badge on
// the fetch/edit/region-label queue icons below — the shared visual marker
// for "this opens a queue", with the base glyph underneath saying which one.
function QueueBadge() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
      <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" /><line x1="4" y1="18" x2="20" y2="18" />
    </svg>
  )
}

// Base icon (18x18) + QueueBadge in the bottom-right corner, on a small
// white disc so the badge stays legible over whatever the base glyph draws
// underneath it.
function WithQueueBadge({ children }) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', width: 18, height: 18 }}>
      <Svg>{children}</Svg>
      <span style={{
        position: 'absolute', right: -3, bottom: -3, width: 12, height: 12, borderRadius: '50%',
        background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 0 0 1px #e5e7eb',
      }}>
        <QueueBadge />
      </span>
    </span>
  )
}

export function ReloadIcon() {
  return (
    <Svg>
      <path d="M3 12a9 9 0 0 1 15.5-6.36L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.36L3 16" />
      <path d="M3 21v-5h5" />
    </Svg>
  )
}

export function FetchQueueIcon() {
  return (
    <WithQueueBadge>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </WithQueueBadge>
  )
}

// Same glyph ScrollList.jsx's own "Edit fields" button uses (see its title="Edit fields")
// — reused here rather than redrawn, so the same action reads as the same icon everywhere.
export function EditQueueIcon() {
  return (
    <WithQueueBadge>
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
    </WithQueueBadge>
  )
}

// A dashed rect with corner-handle dots — the bounding-box annotation
// motif RegionAnnotator.jsx itself draws over an image, in miniature.
export function RegionQueueIcon() {
  return (
    <WithQueueBadge>
      <rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 3" />
      <circle cx="4" cy="4" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="20" cy="4" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="4" cy="20" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="20" cy="20" r="1.4" fill="currentColor" stroke="none" />
    </WithQueueBadge>
  )
}

// A fingertip (rounded capsule) sliding along a dashed trail toward an
// arrowhead — reads as "swipe" for Preview Timeline's item-to-item /
// wheel/arrow-key navigation.
export function SwipeIcon() {
  return (
    <Svg>
      <path d="M3 12h7" strokeDasharray="2 3" />
      <path d="M9 8l4 4-4 4" />
      <rect x="15" y="7" width="5" height="10" rx="2.5" />
    </Svg>
  )
}

// Classic "database" cylinder with a download-style arrow underneath —
// backup = pulling a copy of the DB out.
export function BackupIcon() {
  return (
    <Svg>
      <ellipse cx="12" cy="5" rx="7" ry="2.5" />
      <path d="M5 5v5c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V5" />
      <path d="M12 14v7" />
      <path d="M9 18l3 3 3-3" />
    </Svg>
  )
}

// Simplified brain (two bumpy lobes split by a center line) with a small
// gear badge overlapping — "an AI model gets fitted/processed here".
export function BrainGearIcon() {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', width: 18, height: 18 }}>
      <Svg>
        <path d="M9 3a3 3 0 00-3 3 3 3 0 00-1.86 5.36A3 3 0 006 17h1" />
        <path d="M15 3a3 3 0 013 3 3 3 0 011.86 5.36A3 3 0 0118 17h-1" />
        <path d="M9 3v14M15 3v14" />
      </Svg>
      <span style={{
        position: 'absolute', right: -4, bottom: -4, width: 12, height: 12, borderRadius: '50%',
        background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 0 0 1px #e5e7eb',
      }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <line x1="12" y1="1" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="23" />
          <line x1="1" y1="12" x2="5" y2="12" /><line x1="19" y1="12" x2="23" y2="12" />
          <line x1="4" y1="4" x2="6.8" y2="6.8" /><line x1="17.2" y1="17.2" x2="20" y2="20" />
          <line x1="20" y1="4" x2="17.2" y2="6.8" /><line x1="6.8" y1="17.2" x2="4" y2="20" />
        </svg>
      </span>
    </span>
  )
}
