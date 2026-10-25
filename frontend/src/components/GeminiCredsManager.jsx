import React, { useState, useEffect, useCallback } from 'react'
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

const SOURCE_LABELS = {
  db: 'このUIから保存済み',
  env: '.env (GEMINI_API_KEY)',
  none: '未設定',
}

export default function GeminiCredsManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/gemini_creds/status/', { credentials: 'same-origin' })
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
    if (!apiKey.trim()) {
      setError('APIキーを入力してください')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/gemini_creds/set/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify({ api_key: apiKey.trim() }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setStatus(j)
      setApiKey('')
      setNotice('APIキーを暗号化して保存しました。画像翻訳機能で即座に利用できます。')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function clear() {
    if (!window.confirm('保存されているGemini APIキーを削除しますか？')) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/gemini_creds/set/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify({ clear: true }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `削除に失敗しました (${r.status})`)
      setStatus(j)
      setApiKey('')
      setNotice('UI保存のAPIキーを削除しました。')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function testConnection() {
    setTesting(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/gemini_creds/test/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify({
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok || !j.success) {
        throw new Error(j.detail || `接続テストに失敗しました (${r.status})`)
      }
      setNotice(j.detail || '接続テストに成功しました！APIキーは有効です。')
    } catch (e) {
      setError(e.message)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ maxWidth: 580 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>🌐</span>
            <strong>Gemini API 設定 (画像翻訳)</strong>
            {!loading && status && <StatusLamp active={!!status.configured} />}
          </span>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div className="cgm-panel-error" style={{ marginBottom: 12 }}>{error}</div>}
          {notice && <div className="cgm-panel-notice" style={{ marginBottom: 12 }}>{notice}</div>}

          <div style={{ background: '#0f172a', padding: '12px 16px', borderRadius: 8, marginBottom: 16 }}>
            <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 4 }}>現在の設定状況</div>
            {loading ? (
              <div style={{ color: '#64748b', fontSize: 14 }}>読み込み中…</div>
            ) : status ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontWeight: 600, color: status.configured ? '#4ade80' : '#f87171' }}>
                    {status.configured ? '● 有効' : '○ 未設定'}
                  </span>
                  <span style={{ color: '#cbd5e1' }}>({SOURCE_LABELS[status.source] || status.source})</span>
                </div>
                {status.updated_at && (
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>最終更新: {formatDate(status.updated_at)}</div>
                )}
              </div>
            ) : null}
          </div>

          <div style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.6, marginBottom: 16 }}>
            画像内の外国語（英語・韓国語・中国語）を認識して日本語に翻訳するために使用します。<br />
            <strong>無料枠（クレジットカード登録不要）</strong>で利用可能です。
            <div style={{ marginTop: 6 }}>
              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noreferrer"
                style={{ color: '#60a5fa', textDecoration: 'underline', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                Google AI Studio で無料 API キーを取得 ↗
              </a>
            </div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
              ※ <code>.env</code> に <code>GEMINI_API_KEY=AIzaSy...</code> と直接記載することでも設定できます（このUIで登録したキーが優先されます）。
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 600, color: '#e2e8f0', marginBottom: 6 }}>
              Gemini API キー
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder={status?.configured ? '新しいキーを入力して上書き…' : 'AIzaSy...'}
                style={{
                  flex: 1,
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: 6,
                  color: '#fff',
                  padding: '8px 12px',
                  fontSize: 14,
                }}
              />
              <button
                type="button"
                className="btn"
                onClick={() => setShowKey(v => !v)}
                style={{ padding: '8px 12px', fontSize: 12, background: '#334155', color: '#cbd5e1' }}
                title={showKey ? '隠す' : '表示'}
              >
                {showKey ? '非表示' : '表示'}
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-end', marginTop: 20 }}>
            {status?.source === 'db' && (
              <button
                type="button"
                className="btn"
                onClick={clear}
                disabled={saving || testing}
                style={{ background: '#7f1d1d', color: '#fca5a5', padding: '8px 14px', marginRight: 'auto' }}
              >
                キーを削除
              </button>
            )}
            <button
              type="button"
              className="btn"
              onClick={testConnection}
              disabled={testing || saving || (!apiKey.trim() && !status?.configured)}
              style={{ background: '#334155', color: '#e2e8f0', padding: '8px 16px' }}
            >
              {testing ? 'テスト中…' : '疎通テスト'}
            </button>
            <button
              type="button"
              className="btn"
              onClick={save}
              disabled={saving || testing || !apiKey.trim()}
              style={{ background: '#2563eb', color: '#fff', padding: '8px 20px', fontWeight: 600 }}
            >
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

