import React from 'react'

// Plain determinate progress bar for anywhere "done/total" is already
// known (bulk fetch, etc.) — a percentage number alone doesn't give an
// at-a-glance sense of how far along a long-running operation is nearly
// as well as a filled bar does, especially once `total` climbs into the
// dozens/hundreds.
export default function ProgressBar({ done, total, height = 6 }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 140 }}>
      <div style={{ flex: 1, height, background: '#e5e7eb', borderRadius: height / 2, overflow: 'hidden' }}>
        <div
          style={{
            width: `${pct}%`, height: '100%', background: '#3b82f6', borderRadius: height / 2,
            transition: 'width 0.3s ease',
          }}
        />
      </div>
      <span style={{ fontSize: 12, color: '#6b7280', whiteSpace: 'nowrap' }}>{pct}%</span>
    </div>
  )
}
