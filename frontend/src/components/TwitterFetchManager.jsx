import React, { useState } from 'react'
import RetweetFetchManager from './RetweetFetchManager'
import BookmarkFetchManager from './BookmarkFetchManager'

const TABS = [
  { key: 'bookmark', label: 'ブックマーク' },
  { key: 'retweet', label: 'RT' },
]

// Single entry point ("Twitterから画像取得") for the two kinds of manual,
// on-demand Twitter/X bulk fetch this app supports, replacing what used
// to be two separate header-menu items/modals (RetweetFetchManager.jsx /
// BookmarkFetchManager.jsx as their own standalone panels). Each tab is
// still that exact same component/logic, just rendered `embedded` (no
// backdrop/header of its own) inside this shared shell — only which tab
// is showing is new state here.
export default function TwitterFetchManager({ onClose, onEnqueueFetch }) {
  const [tab, setTab] = useState('bookmark')

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>Twitterから画像取得</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: 'flex', gap: 4, padding: '0 20px', borderBottom: '1px solid #334155' }}>
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                padding: '10px 16px', fontSize: 13,
                color: tab === t.key ? '#f1f5f9' : '#94a3b8',
                borderBottom: tab === t.key ? '2px solid #3b82f6' : '2px solid transparent',
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="cgm-panel-body">
          {tab === 'bookmark' && <BookmarkFetchManager embedded onEnqueueFetch={onEnqueueFetch} />}
          {tab === 'retweet' && <RetweetFetchManager embedded onEnqueueFetch={onEnqueueFetch} />}
        </div>
      </div>
    </div>
  )
}
