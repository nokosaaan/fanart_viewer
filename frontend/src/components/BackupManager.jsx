import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function formatSize(bytes) {
  const n = Number(bytes)
  if (!n) return '—'
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('ja-JP')
}

// Google Drive backup/restore panel. Opened from the app header (admin only).
export default function BackupManager({ onClose }) {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [restoringId, setRestoringId] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/backup/list/', { credentials: 'same-origin' })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j.detail || `一覧取得失敗 (${r.status})`)
      }
      const j = await r.json()
      setFiles(j.files || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function createBackup() {
    setCreating(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/backup/create/', { method: 'POST', headers: HEADERS, credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `バックアップ失敗 (${r.status})`)
      setNotice(`バックアップ完了: ${j.name}`)
      load()
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  async function restoreBackup(file) {
    if (!window.confirm(
      `「${file.name}」から復元しますか？\n\n` +
      'この操作はデータベースが空の場合のみ実行できます（デバイス移行時の初回投入専用）。\n' +
      '既存データがある場合は失敗します。'
    )) return

    setRestoringId(file.id)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/backup/restore/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ file_id: file.id }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `復元失敗 (${r.status})`)
      setNotice('復元が完了しました。ページを再読み込みしてください。')
    } catch (e) {
      setError(e.message)
    } finally {
      setRestoringId(null)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>データベースバックアップ (Google Drive)</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          <div style={{ marginBottom: 16 }}>
            <button className="btn" onClick={createBackup} disabled={creating}>
              {creating ? 'バックアップ中…' : '今すぐバックアップ'}
            </button>
          </div>

          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 8 }}>
            バックアップ一覧（新しい順）
          </div>

          {loading ? (
            <div>読み込み中…</div>
          ) : files.length === 0 ? (
            <div className="cgm-empty-hint">バックアップがありません</div>
          ) : (
            <div>
              {files.map(f => (
                <div key={f.id} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 0', borderBottom: '1px solid #334155',
                }}>
                  <div>
                    <div>{f.name}</div>
                    <div style={{ fontSize: 12, color: '#94a3b8' }}>
                      {formatDate(f.createdTime)} · {formatSize(f.size)}
                    </div>
                  </div>
                  <button
                    className="btn"
                    style={{ fontSize: 12 }}
                    disabled={restoringId === f.id}
                    onClick={() => restoreBackup(f)}
                  >
                    {restoringId === f.id ? '復元中…' : '復元'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
