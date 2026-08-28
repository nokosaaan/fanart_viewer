import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ja-JP')
}

const SOURCE_LABELS = { db: 'このUIから保存済み', env: '.env (TWITTER_AUTH_TOKEN/CT0)', none: '未設定' }

// Admin-only panel to set the Twitter/X session cookies (auth_token/ct0)
// used by the scraping fetchers, replacing manual .env edits + container
// recreation. Deliberately write-only: there is no endpoint that returns the
// stored value, so this panel never fetches/shows the actual cookies —
// only whether something is configured and when it changed (see
// twitter_creds_views.py).
export default function TwitterCredsManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [authToken, setAuthToken] = useState('')
  const [ct0, setCt0] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/twitter_creds/status/', { credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `状態の取得に失敗しました (${r.status})`)
      setStatus(j)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function save() {
    if (!authToken.trim() || !ct0.trim()) { setError('auth_token と ct0 の両方を入力してください'); return }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/twitter_creds/set/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ auth_token: authToken.trim(), ct0: ct0.trim() }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setStatus(j)
      setAuthToken('')
      setCt0('')
      setNotice('保存しました。次回のfetchから即座にこの認証情報が使われます(再起動不要)。')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>Twitter/X 認証情報</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            センシティブ/非公開アカウントの取得やRT一括取得に使うx.comのセッションCookieです。
            ブラウザでx.comにログインした状態でDevTools → Application → Cookiesから
            <code style={{ margin: '0 4px' }}>auth_token</code>と<code style={{ margin: '0 4px' }}>ct0</code>をコピーしてください。
            保存した値はサーバー側で暗号化して保存され、この画面を含めどこにも読み出し表示はされません(書き込み専用)。
          </div>

          <div style={{ fontSize: 13, marginBottom: 16, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
            {loading ? '状態を確認中…' : status ? (
              <>現在の設定: <strong>{status.configured ? '設定済み' : '未設定'}</strong>
                {status.configured && <> ({SOURCE_LABELS[status.source] || status.source})</>}
                {status.updated_at && <> — 最終更新 {formatDate(status.updated_at)}</>}
              </>
            ) : '—'}
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>auth_token</label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={authToken} onChange={e => setAuthToken(e.target.value)}
              placeholder="新しい auth_token"
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>ct0</label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={ct0} onChange={e => setCt0(e.target.value)}
              placeholder="新しい ct0"
            />
          </div>

          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
            onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
