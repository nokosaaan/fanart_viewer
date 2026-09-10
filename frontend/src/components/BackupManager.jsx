import React, { useState, useEffect, useCallback, useRef } from 'react'
import ProgressBar from './ProgressBar'

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

const PHASE_LABELS = {
  starting: '準備中…',
  sqlite_backup: 'DBスナップショット中…',
  dumping: 'ダンプ中…',
  compressing: '圧縮中…',
  uploading: 'Google Driveへアップロード中…',
  downloading: 'Google Driveからダウンロード中…',
  importing: 'データベースへ反映中…',
}

// Google Drive backup/restore panel. Opened from the app header (admin only).
export default function BackupManager({ onClose }) {
  const [files, setFiles] = useState([])
  const [folderUrl, setFolderUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [restoringId, setRestoringId] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // Set when the server refuses a restore because the DB already has data;
  // holds the file plus both sides' row counts so the user can compare
  // before choosing to overwrite.
  const [confirmState, setConfirmState] = useState(null)
  // Backup used to be a single blocking POST with no progress at all —
  // now the server runs it on a background thread (see backup_progress.py)
  // and this polls its status instead, so a long backup (a big DB, a slow
  // home upload connection) shows real phase/percent instead of a static
  // spinner for however long it takes.
  const [backupStatus, setBackupStatus] = useState(null)
  const pollRef = useRef(null)
  // Same background+poll treatment as backup, for restore's own
  // (potentially just as slow) Drive download — see restore_progress.py.
  const [restoreStatus, setRestoreStatus] = useState(null)
  const restorePollRef = useRef(null)
  // restore_progress.py's state has no idea which `file` a restore was
  // FOR (just a file_id string) — this is purely local, so confirmState
  // (which needs the file's display name too) can be reconstructed from
  // whichever file doRestore was last called with.
  const pendingFileRef = useRef(null)

  // Google Drive OAuth client — the exe build has no .env a user could
  // edit, so this replaces scripts/google_drive_auth.py's out-of-band
  // flow with the same OAuth consent flow, triggered from here (see
  // drive_creds_views.authenticate).
  const [driveStatus, setDriveStatus] = useState(null)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [authenticating, setAuthenticating] = useState(false)
  const [authError, setAuthError] = useState('')
  const [authNotice, setAuthNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')

    try {
      const rd = await fetch('/api/drive_creds/status/', { credentials: 'same-origin' })
      if (rd.ok) setDriveStatus(await rd.json())
    } catch (_) {}

    try {
      const r = await fetch('/api/backup/list/', { credentials: 'same-origin' })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j.detail || `一覧取得失敗 (${r.status})`)
      }
      const j = await r.json()
      setFiles(j.files || [])
      setFolderUrl(j.folder_url || '')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const pollBackupStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/backup/status/', { credentials: 'same-origin' })
      if (!r.ok) return null
      const j = await r.json()
      setBackupStatus(j)
      if (!j.running && pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
        load()  // pick up the newly-created file in the list once it's done
      }
      return j
    } catch (_) {
      return null
    }
  }, [load])

  function startPolling() {
    if (pollRef.current) return
    pollRef.current = setInterval(pollBackupStatus, 1000)
  }

  // Reopening the panel while a backup started earlier is still running
  // (e.g. closed the panel, came back later) should resume showing its
  // progress instead of looking like nothing is happening — the backup
  // itself keeps running server-side regardless of whether this panel is
  // open (see backup_progress.py), so this is purely about not losing
  // track of it from here.
  useEffect(() => {
    pollBackupStatus().then(j => { if (j && j.running) startPolling() })
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function authenticateDrive() {
    setAuthenticating(true)
    setAuthError('')
    setAuthNotice('')
    try {
      const r = await fetch('/api/drive_creds/authenticate/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ client_id: clientId.trim(), client_secret: clientSecret.trim() }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `認証に失敗しました (${r.status})`)
      setDriveStatus(j)
      setClientId('')
      setClientSecret('')
      setAuthNotice('認証に成功しました。')
      load()
    } catch (e) {
      setAuthError(e.message)
    } finally {
      setAuthenticating(false)
    }
  }

  async function createBackup() {
    setCreating(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/backup/create/', { method: 'POST', headers: HEADERS, credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `バックアップ失敗 (${r.status})`)
      setBackupStatus(j)
      startPolling()
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  // Watches backupStatus (kept live by the polling above) for the moment a
  // run that WAS in progress finishes, so the success/failure notice shows
  // up exactly once — not on every poll tick while it's still running, and
  // not missed if this panel was reopened mid-run rather than having
  // started it itself.
  const prevRunningRef = useRef(false)
  useEffect(() => {
    const wasRunning = prevRunningRef.current
    prevRunningRef.current = !!backupStatus?.running
    if (wasRunning && backupStatus && !backupStatus.running) {
      if (backupStatus.error) setError(backupStatus.error)
      else if (backupStatus.result) setNotice(`バックアップ完了: ${backupStatus.result.name}`)
    }
  }, [backupStatus])

  // `react`: whether to act on a terminal (not-running) result — true only
  // while actively polling a restore THIS session started (or resumed
  // because it was already running on mount). The one-off mount-time
  // check below always passes false: needs_confirmation/result/error
  // persist server-side until the next start(), so reacting to a
  // terminal state found only on mount (nothing was ever polling) would
  // re-show a confirmation dialog or "restore complete" notice for an
  // attempt the user already saw and dismissed in an earlier visit here.
  const pollRestoreStatus = useCallback(async (react) => {
    try {
      const r = await fetch('/api/backup/restore/status/', { credentials: 'same-origin' })
      if (!r.ok) return null
      const j = await r.json()
      setRestoreStatus(j)
      if (!j.running) {
        if (restorePollRef.current) {
          clearInterval(restorePollRef.current)
          restorePollRef.current = null
        }
        if (react) {
          const file = pendingFileRef.current
          if (j.needs_confirmation && file) {
            setConfirmState({ file, current: j.needs_confirmation.current, backup: j.needs_confirmation.backup })
          } else if (j.error) {
            setError(j.error)
            setRestoringId(null)
          } else if (j.result !== null) {
            // result is {} for strict/overwrite, or the merge summary dict
            setConfirmState(null)
            setRestoringId(null)
            if (j.result && Object.keys(j.result).length > 0) {
              const mr = j.result
              setNotice(
                `追記が完了しました: アイテム${mr.items_added}件追加` +
                (mr.items_skipped ? `(重複${mr.items_skipped}件はスキップ)` : '') +
                `、プレビュー画像${mr.previews_added}件、キャラクターグループ${mr.groups_added}件追加。ページを再読み込みしてください。`
              )
            } else {
              setNotice('復元が完了しました。ページを再読み込みしてください。')
            }
          }
        }
      }
      return j
    } catch (_) {
      return null
    }
  }, [])

  function startRestorePolling() {
    if (restorePollRef.current) return
    restorePollRef.current = setInterval(() => pollRestoreStatus(true), 1000)
  }

  // Only resumes polling for an ALREADY-RUNNING restore (mirrors backup's
  // own mount-resume effect) — see pollRestoreStatus's own comment on why
  // `react` is false here specifically.
  useEffect(() => {
    pollRestoreStatus(false).then(j => { if (j && j.running) startRestorePolling() })
    return () => { if (restorePollRef.current) clearInterval(restorePollRef.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function doRestore(file, mode) {
    pendingFileRef.current = file
    setRestoringId(file.id)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/backup/restore/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ file_id: file.id, mode }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `復元失敗 (${r.status})`)
      setRestoreStatus(j)
      startRestorePolling()
    } catch (e) {
      setError(e.message)
      setRestoringId(null)
    }
  }

  function restoreBackup(file) {
    doRestore(file, 'strict')
  }

  function confirmOverwrite() {
    if (!confirmState) return
    doRestore(confirmState.file, 'overwrite')
  }

  function confirmMerge() {
    if (!confirmState) return
    doRestore(confirmState.file, 'merge')
  }

  function cancelOverwrite() {
    setConfirmState(null)
    setRestoringId(null)
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>データベースバックアップ (Google Drive)</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          <div style={{ marginBottom: 20, padding: '12px', border: '1px solid #334155', borderRadius: 6 }}>
            <div style={{ fontSize: 13, marginBottom: 10 }}>
              {driveStatus == null ? '状態を確認中…' : (
                <>Google Drive: <strong>{driveStatus.configured ? '認証済み' : '未認証'}</strong>
                  {driveStatus.configured && driveStatus.source === 'env' && <> (.env)</>}
                  {driveStatus.updated_at && <> — 最終認証 {formatDate(driveStatus.updated_at)}</>}
                </>
              )}
            </div>
            {authError && <div style={{ color: '#f87171', marginBottom: 8, fontSize: 13 }}>{authError}</div>}
            {authNotice && <div style={{ color: '#4ade80', marginBottom: 8, fontSize: 13 }}>{authNotice}</div>}
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 10 }}>
              Google CloudコンソールでOAuthクライアント(種類: デスクトップアプリ)を作成し、
              そのクライアントIDとシークレットを入力して認証してください。
              「認証する」を押すとブラウザが開き、Googleのログイン・許可画面が表示されます
              (すでに登録済みの場合は空欄のまま再認証できます)。
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <input
                type="text" autoComplete="off" placeholder="Client ID"
                value={clientId} onChange={e => setClientId(e.target.value)}
                style={{ flex: '1 1 240px', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
              />
              <input
                type="password" autoComplete="off" placeholder="Client Secret"
                value={clientSecret} onChange={e => setClientSecret(e.target.value)}
                style={{ flex: '1 1 240px', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
              />
            </div>
            <button className="btn" style={{ fontSize: 13 }} onClick={authenticateDrive} disabled={authenticating}>
              {authenticating ? 'ブラウザで認証してください…' : (driveStatus?.configured ? '再認証する' : '認証する')}
            </button>
          </div>

          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          {confirmState ? (
            <div>
              <div style={{ marginBottom: 12 }}>
                データベースに既存データがあります。「{confirmState.file.name}」の内容と比較してください。<br />
                <strong style={{ color: '#f87171' }}>上書き</strong>すると現在のデータは失われ、バックアップの内容に置き換わります。<br />
                <strong style={{ color: '#4ade80' }}>追記</strong>すると現在のデータは残したまま、バックアップ側の新しいアイテムだけを追加します
                (同じ投稿(external_id+source一致)はスキップされ、重複しては追加されません — 別デバイスで育てたアーカイブを合体させたい場合に使います)。
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
                  style={{ background: '#16a34a', borderColor: '#16a34a' }}
                  onClick={confirmMerge}
                  disabled={restoringId === confirmState.file.id}
                >
                  {restoringId === confirmState.file.id ? '処理中…' : '追記して復元'}
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
          <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <button className="btn" onClick={createBackup} disabled={creating || backupStatus?.running}>
              {creating ? '開始しています…' : backupStatus?.running ? 'バックアップ中…' : '今すぐバックアップ'}
            </button>
            {backupStatus?.running && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 12, color: '#94a3b8' }}>{PHASE_LABELS[backupStatus.phase] || '処理中…'}</span>
                {backupStatus.percent != null ? (
                  <ProgressBar done={backupStatus.percent} total={100} />
                ) : (
                  <span style={{ fontSize: 12, color: '#94a3b8' }}>(進捗率は不明 — 完了までお待ちください)</span>
                )}
              </div>
            )}
            {folderUrl && (
              <a href={folderUrl} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13 }}>
                Google Driveフォルダを開く ↗
              </a>
            )}
          </div>

          {restoreStatus?.running && (
            <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 12, color: '#94a3b8' }}>復元中: {PHASE_LABELS[restoreStatus.phase] || '処理中…'}</span>
              {restoreStatus.percent != null ? (
                <ProgressBar done={restoreStatus.percent} total={100} />
              ) : (
                <span style={{ fontSize: 12, color: '#94a3b8' }}>(進捗率は不明 — 完了までお待ちください)</span>
              )}
            </div>
          )}

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
                      {' · '}
                      <a href={`https://drive.google.com/file/d/${f.id}/view`} target="_blank" rel="noopener noreferrer">
                        Driveで開く
                      </a>
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
