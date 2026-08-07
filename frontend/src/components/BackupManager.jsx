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

const TABLE_LABELS = {
  item_charactergroup: 'キャラクターグループ',
  item_item: 'アイテム',
  item_previewimage: 'プレビュー画像',
}

// Google Drive backup/restore panel. Opened from the app header (admin only).
export default function BackupManager({ onClose }) {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [restoringId, setRestoringId] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // Set when the server refuses a restore because the DB already has data;
  // holds the file plus both sides' row counts so the user can compare
  // before choosing to overwrite.
  const [confirmState, setConfirmState] = useState(null)

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

  async function doRestore(file, overwrite) {
    setRestoringId(file.id)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/backup/restore/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ file_id: file.id, overwrite }),
      })
      const j = await r.json().catch(() => ({}))
      if (r.status === 409 && j.needs_confirmation) {
        setConfirmState({ file, current: j.current, backup: j.backup })
        return
      }
      if (!r.ok) throw new Error(j.detail || `復元失敗 (${r.status})`)
      setConfirmState(null)
      setNotice('復元が完了しました。ページを再読み込みしてください。')
    } catch (e) {
      setError(e.message)
    } finally {
      setRestoringId(null)
    }
  }

  function restoreBackup(file) {
    doRestore(file, false)
  }

  function confirmOverwrite() {
    if (!confirmState) return
    doRestore(confirmState.file, true)
  }

  function cancelOverwrite() {
    setConfirmState(null)
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>データベースバックアップ (Google Drive)</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          {confirmState ? (
            <div>
              <div style={{ marginBottom: 12 }}>
                データベースに既存データがあります。「{confirmState.file.name}」の内容と比較してください。
                上書きすると<strong style={{ color: '#f87171' }}>現在のデータは失われ</strong>、バックアップの内容に置き換わります。
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 16, fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #334155' }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px' }}></th>
                    <th style={{ textAlign: 'right', padding: '4px 8px' }}>現在のDB</th>
                    <th style={{ textAlign: 'right', padding: '4px 8px' }}>このバックアップ</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(TABLE_LABELS).map(key => (
                    <tr key={key} style={{ borderBottom: '1px solid #334155' }}>
                      <td style={{ padding: '4px 8px', color: '#94a3b8' }}>{TABLE_LABELS[key]}</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right' }}>{confirmState.current[key] ?? 0}件</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right' }}>{confirmState.backup[key] ?? 0}件</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button className="btn" onClick={cancelOverwrite} disabled={restoringId === confirmState.file.id}>
                  キャンセル
                </button>
                <button
                  className="btn"
                  style={{ background: '#ef4444', borderColor: '#ef4444' }}
                  onClick={confirmOverwrite}
                  disabled={restoringId === confirmState.file.id}
                >
                  {restoringId === confirmState.file.id ? '復元中…' : '上書きして復元'}
                </button>
              </div>
            </div>
          ) : (
          <>
          <div style={{ marginBottom: 16 }}>
            <button className="btn" onClick={createBackup} disabled={creating}>
              {creating ? 'バックアップ中…' : '今すぐバックアップ'}
            </button>
          </div>

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
          </>
          )}
        </div>
      </div>
    </div>
  )
}
