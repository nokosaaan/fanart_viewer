import React from 'react'

// Small "is this actually authenticated" indicator for the Twitter/Pixiv/
// Poipiku credential panels — a single glance at a colored dot instead of
// having to read a "設定済み/未設定" status line to know whether the
// browser-login step still needs doing.
export default function StatusLamp({ active, activeLabel = '認証済み', inactiveLabel = '未認証' }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600 }}>
      <span style={{
        width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
        background: active ? '#22c55e' : '#64748b',
        boxShadow: active ? '0 0 6px rgba(34,197,94,0.8)' : 'none',
      }} />
      <span style={{ color: active ? '#4ade80' : '#94a3b8' }}>{active ? activeLabel : inactiveLabel}</span>
    </span>
  )
}
