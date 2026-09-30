import React, { useState, useEffect, useCallback } from 'react'
import PollerSettingsPanel from './PollerSettingsPanel'
import BrowserLoginPanel from './BrowserLoginPanel'
import StatusLamp from './StatusLamp'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ja-JP')
}

const SOURCE_LABELS = { db: 'このUIから保存済み', env: '.env (POIPIKU_LK/JSESSIONID)', none: '未設定' }

// Admin-only panel to set Poipiku login cookies used by
// item.poipiku_fetch. Mirrors TwitterCredsManager.jsx/PixivCredsManager.jsx:
// write-only, so this panel never fetches/shows the actual stored values —
// only whether something is configured and when it changed (see
// poipiku_creds_views.py).
export default function PoipikuCredsManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lk, setLk] = useState('')
  const [jsessionid, setJsessionid] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pollStatus, setPollStatus] = useState(null)
  const [manualOpen, setManualOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/poipiku_creds/status/', { credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `状態の取得に失敗しました (${r.status})`)
      setStatus(j)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }

    // Best-effort — the poller may not be enabled, so a failure here
    // shouldn't block the panel itself.
    try {
      const r2 = await fetch('/api/poipiku_poll/status/', { credentials: 'same-origin' })
      if (r2.ok) setPollStatus(await r2.json())
    } catch (_) {}
  }, [])

  useEffect(() => { load() }, [load])

  async function save() {
    if (!lk.trim() && !jsessionid.trim()) {
      setError('LK か JSESSIONID のいずれかを入力してください')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/poipiku_creds/set/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({
          ...(lk.trim() ? { lk: lk.trim() } : {}),
          ...(jsessionid.trim() ? { jsessionid: jsessionid.trim() } : {}),
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setStatus(j)
      setLk('')
      setJsessionid('')
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
          <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <strong>Poipiku 認証情報</strong>
            {!loading && status && <StatusLamp active={!!status.configured} />}
          </span>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 11, fontWeight: 700, color: '#64748b', letterSpacing: '0.04em', marginBottom: 8 }}>
            手順 1 — ブラウザでログイン
          </div>
          <BrowserLoginPanel platform="poipiku" onApplied={j => { setStatus(j); setNotice('ログインを検知し、認証情報を自動保存しました。次回のfetchから即座に使われます(再起動不要)。') }} />

          <div style={{ fontSize: 13, margin: '16px 0', padding: '8px 12px', background: '#0f172a', color: '#e2e8f0', borderRadius: 6 }}>
            {loading ? '状態を確認中…' : status ? (
              <>現在の設定: <strong>{status.configured ? '設定済み' : '未設定'}</strong>
                {status.configured && <> ({SOURCE_LABELS[status.source] || status.source})</>}
                {status.updated_at && <> — 最終更新 {formatDate(status.updated_at)}</>}
                <br />LK: <strong>{status.has_lk ? '設定済み' : '未設定'}</strong>
                {' '}/ JSESSIONID: <strong>{status.has_jsessionid ? '設定済み' : '未設定'}</strong>
              </>
            ) : '—'}
          </div>

          <div style={{ fontSize: 11, fontWeight: 700, color: '#64748b', letterSpacing: '0.04em', margin: '20px 0 8px' }}>
            手順 2 — 自動取得の設定(任意)
          </div>
          <PollerSettingsPanel platform="poipiku" label="自動でお気に入りを取得する" />

          {pollStatus && (
            <div style={{ fontSize: 13, margin: '12px 0 0', padding: '8px 12px', background: '#0f172a', color: '#e2e8f0', borderRadius: 6 }}>
              <div style={{ marginBottom: 4 }}>
                お気に入り自動取得: 最終成功 {formatDate(pollStatus.last_success_at)}
                {' '}— 未処理キュー {pollStatus.pending_count}件
              </div>
              {pollStatus.consecutive_failures > 0 && (
                <div style={{ color: '#f87171' }}>
                  {pollStatus.consecutive_failures}回連続で失敗中 ({formatDate(pollStatus.last_error_at)}): {pollStatus.last_error}
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid #334155' }}>
            <button
              type="button" className="btn" style={{ background: 'transparent', color: '#94a3b8', padding: '4px 0' }}
              onClick={() => setManualOpen(o => !o)}
            >
              {manualOpen ? '▾' : '▸'} 手動で入力する場合(ブラウザログインが使えないとき)
            </button>

            {manualOpen && (
              <div style={{ marginTop: 12 }}>
                <ul style={{ fontSize: 12, color: '#94a3b8', margin: '0 0 16px', paddingLeft: 18, lineHeight: 1.7 }}>
                  <li>ブラウザでpoipiku.comにログインした状態で、DevTools → Application → Cookiesを開く</li>
                  <li><code>POIPIKU_LK</code>(長期ログインキー、通常はこれだけで十分)と<code>JSESSIONID</code>(任意)をコピーして下に貼り付ける</li>
                  <li>保存した値はサーバー側で暗号化され、この画面を含めどこにも読み出し表示されません(書き込み専用)</li>
                </ul>

                <div style={{ marginBottom: 14 }}>
                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>LK(推奨)</label>
                  <input
                    type="password" autoComplete="off"
                    style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                      borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
                    value={lk} onChange={e => setLk(e.target.value)}
                    placeholder="新しい POIPIKU_LK"
                  />
                </div>

                <div style={{ marginBottom: 18 }}>
                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                    JSESSIONID(任意 — 未入力なら既存の設定を変更しません)
                  </label>
                  <input
                    type="password" autoComplete="off"
                    style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                      borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
                    value={jsessionid} onChange={e => setJsessionid(e.target.value)}
                    placeholder="新しい JSESSIONID"
                  />
                </div>

                <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                  onClick={save} disabled={saving}>
                  {saving ? '保存中…' : '保存'}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
