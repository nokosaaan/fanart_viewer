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

const SOURCE_LABELS = { db: 'このUIから保存済み', env: '.env (PIXIV_PHPSESSID/USER/PASS)', none: '未設定' }

// Admin-only panel to set Pixiv login (PHPSESSID cookie, or user/pass) used
// by playwright_helper.py's Pixiv fetcher. Mirrors TwitterCredsManager.jsx:
// write-only, so this panel never fetches/shows the actual stored values —
// only whether something is configured and when it changed (see
// pixiv_creds_views.py).
export default function PixivCredsManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [phpsessid, setPhpsessid] = useState('')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/pixiv_creds/status/', { credentials: 'same-origin' })
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
    if (!phpsessid.trim() && !(user.trim() && password.trim())) {
      setError('PHPSESSID か、ユーザー名とパスワードの両方を入力してください')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/pixiv_creds/set/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({
          ...(phpsessid.trim() ? { phpsessid: phpsessid.trim() } : {}),
          ...(user.trim() ? { user: user.trim() } : {}),
          ...(password.trim() ? { password: password.trim() } : {}),
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setStatus(j)
      setPhpsessid('')
      setUser('')
      setPassword('')
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
          <strong>Pixiv 認証情報</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            R18作品の取得やPixivログインが必要な取得に使う認証情報です。
            ブラウザでpixiv.netにログインした状態でDevTools → Application → Cookiesから
            <code style={{ margin: '0 4px' }}>PHPSESSID</code>をコピーするか、
            ユーザー名とパスワードの組を入力してください(PHPSESSIDの方が確実です)。
            保存した値はサーバー側で暗号化して保存され、この画面を含めどこにも読み出し表示はされません(書き込み専用)。
          </div>

          <div style={{ fontSize: 13, marginBottom: 16, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
            {loading ? '状態を確認中…' : status ? (
              <>現在の設定: <strong>{status.configured ? '設定済み' : '未設定'}</strong>
                {status.configured && <> ({SOURCE_LABELS[status.source] || status.source})</>}
                {status.updated_at && <> — 最終更新 {formatDate(status.updated_at)}</>}
                <br />PHPSESSID: <strong>{status.has_phpsessid ? '設定済み' : '未設定'}</strong>
                {' '}/ ユーザー名+パスワード: <strong>{status.has_user_pass ? '設定済み' : '未設定'}</strong>
              </>
            ) : '—'}
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>PHPSESSID(推奨)</label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={phpsessid} onChange={e => setPhpsessid(e.target.value)}
              placeholder="新しい PHPSESSID"
            />
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
              ユーザー名(任意 — 未入力なら既存の設定を変更しません)
            </label>
            <input
              type="text" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={user} onChange={e => setUser(e.target.value)}
              placeholder="新しいユーザー名/メールアドレス"
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
              パスワード(任意 — 未入力なら既存の設定を変更しません)
            </label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={password} onChange={e => setPassword(e.target.value)}
              placeholder="新しいパスワード"
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
