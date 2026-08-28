import React, { useState } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Kicks off a background scan of an account's timeline for retweets (see
// twitter_gql_fetch.fetch_account_retweets) and archives any not already in
// the DB. The scan+download itself runs server-side as a fire-and-forget
// background job (same pattern as bookmark_fetch) — this panel only starts
// it and reports back that it started, it does not show live progress.
export default function RetweetFetchManager({ onClose }) {
  const [screenName, setScreenName] = useState('')
  const [maxItems, setMaxItems] = useState(30)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function submit() {
    const name = screenName.trim().replace(/^@/, '')
    if (!name) { setError('アカウント名を入力してください'); return }
    setSubmitting(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ screen_name: name, max_items: maxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setNotice(`@${name} のRT取得をバックグラウンドで開始しました（最大${maxItems}件）。完了しても通知は出ないので、しばらくしてから一覧を再読み込みしてください。`)
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>アカウントのRTをまとめて取得</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            指定アカウントのタイムラインをさかのぼり、(引用ではない)RTを新しい順に集めて自動でアイテム登録します。
            既に登録済みのツイートはスキップされます。レート制限保護のため一度に取得する件数には上限があります。
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>アカウント (@なし)</label>
            <input
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={screenName} onChange={e => setScreenName(e.target.value)}
              placeholder="screen_name"
              onKeyDown={e => { if (e.key === 'Enter') submit() }}
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
              取得件数の上限(目安: 30〜40件程度が安全)
            </label>
            <input
              type="number" min={1} max={100}
              style={{ width: 120, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={maxItems} onChange={e => setMaxItems(Math.max(1, Math.min(100, parseInt(e.target.value, 10) || 1)))}
            />
          </div>

          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
            onClick={submit} disabled={submitting}>
            {submitting ? '開始中…' : '取得を開始'}
          </button>
        </div>
      </div>
    </div>
  )
}
