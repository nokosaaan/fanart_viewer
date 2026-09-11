import React, { useState, useEffect, useCallback, useRef } from 'react'
import ProgressBar from './ProgressBar'
import GoogleCloudSetupHelp from './GoogleCloudSetupHelp'

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

// Snapshot the last-known status client-side so a reload of the whole
// page (the app itself tells the user to do exactly this once a restore
// finishes, to pick up the new DB) doesn't show a blank/reset panel for
// the brief gap before the resume-poll below completes — these are only
// ever used to seed the very first render; the live poll that always
// fires immediately on mount corrects them a moment later regardless.
function loadCachedStatus(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch (_) {
    return null
  }
}
function saveCachedStatus(key, status) {
  try {
    localStorage.setItem(key, JSON.stringify(status))
  } catch (_) {}
}
const BACKUP_STATUS_CACHE_KEY = 'fv_backup_status_cache'
const RESTORE_STATUS_CACHE_KEY = 'fv_restore_status_cache'
// Which file a restore attempt is FOR (see pendingFileRef below) also has
// to survive a reload — restore_progress.py's own state has no idea
// which file it's restoring, and a reload wipes the in-memory ref that
// used to be the only place that was recorded, which meant a reload at
// exactly the wrong moment (waiting on a large Drive download) lost track
// of which file the eventual needs_confirmation was even about, so the
// overwrite/追記/キャンセル dialog silently never appeared.
const RESTORE_PENDING_FILE_KEY = 'fv_restore_pending_file'

const TABLE_LABELS = {
  item_charactergroup: 'キャラクターグループ',
  item_item: 'アイテム',
  item_previewimage: 'プレビュー画像',
  item_characterdanboorulink: 'キャラ↔Danbooruリンク',
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
  // Seeded from the last-known restore file/status (see RESTORE_PENDING_FILE_KEY
  // above) so a reload mid-restore keeps showing "復元中…" on the right row
  // instead of looking like nothing is happening until the resume-poll lands.
  const [restoringId, setRestoringId] = useState(() => {
    const cachedRestore = loadCachedStatus(RESTORE_STATUS_CACHE_KEY)
    const cachedFile = loadCachedStatus(RESTORE_PENDING_FILE_KEY)
    return cachedRestore?.running && cachedFile ? cachedFile.id : null
  })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // Set when the server refuses a restore because the DB already has data;
  // holds the file plus both sides' row counts so the user can compare
  // before choosing to overwrite.
  const [confirmState, setConfirmState] = useState(null)
  // Which confirm-dialog button the user actually clicked ('merge' |
  // 'overwrite' | null) -- separate from restoringId, which is also true
  // throughout the initial strict-mode CHECK that produced this dialog in
  // the first place. Using restoringId alone for the buttons' busy label
  // made both the 追記 and 上書き buttons render as already "処理中…"/
  // "復元中…" the instant the dialog appeared, before the user had clicked
  // either one.
  const [confirmAction, setConfirmAction] = useState(null)
  // Backup used to be a single blocking POST with no progress at all —
  // now the server runs it on a background thread (see backup_progress.py)
  // and this polls its status instead, so a long backup (a big DB, a slow
  // home upload connection) shows real phase/percent instead of a static
  // spinner for however long it takes.
  const [backupStatus, setBackupStatus] = useState(() => loadCachedStatus(BACKUP_STATUS_CACHE_KEY))
  const pollRef = useRef(null)
  // Same background+poll treatment as backup, for restore's own
  // (potentially just as slow) Drive download — see restore_progress.py.
  const [restoreStatus, setRestoreStatus] = useState(() => loadCachedStatus(RESTORE_STATUS_CACHE_KEY))
  const restorePollRef = useRef(null)
  // restore_progress.py's state has no idea which `file` a restore was
  // FOR (just a file_id string) — this is purely local. Read/written
  // through readPendingFile()/writePendingFile() below (not directly),
  // which also mirror it to RESTORE_PENDING_FILE_KEY: a plain useRef
  // would otherwise be reset to null by exactly the reload this app
  // itself tells the user to do once a restore finishes, losing track of
  // which file a still-running restore (e.g. a second, slower one
  // started right after) was even for — silently swallowing the
  // overwrite/追記/キャンセル confirmation once it eventually came back.
  const pendingFileRef = useRef(null)

  function readPendingFile() {
    if (pendingFileRef.current) return pendingFileRef.current
    return loadCachedStatus(RESTORE_PENDING_FILE_KEY)
  }
  function writePendingFile(file) {
    pendingFileRef.current = file
    if (file) saveCachedStatus(RESTORE_PENDING_FILE_KEY, file)
    else try { localStorage.removeItem(RESTORE_PENDING_FILE_KEY) } catch (_) {}
  }

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
  const [showGoogleCloudHelp, setShowGoogleCloudHelp] = useState(false)

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
      saveCachedStatus(BACKUP_STATUS_CACHE_KEY, j)
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
      saveCachedStatus(RESTORE_STATUS_CACHE_KEY, j)
      if (!j.running) {
        if (restorePollRef.current) {
          clearInterval(restorePollRef.current)
          restorePollRef.current = null
        }
        // Every branch below is gated on readPendingFile() (not just the
        // needs_confirmation one) — this is what a fresh mount's react=true
        // resume-poll relies on to avoid resurrecting a stale error/result
        // left over from some unrelated earlier restore attempt: the server
        // state persists indefinitely until the next start(), but the
        // pending-file marker is only ever set while THIS attempt is still
        // unresolved, and cleared the instant it's handled either here or
        // via cancelOverwrite().
        if (react) {
          const file = readPendingFile()
          if (file && j.needs_confirmation) {
            setConfirmState({ file, current: j.needs_confirmation.current, backup: j.needs_confirmation.backup })
            setConfirmAction(null)
          } else if (file && j.error) {
            setError(j.error)
            setRestoringId(null)
            setConfirmAction(null)
            writePendingFile(null)
          } else if (file && j.result !== null) {
            // result is {} for strict/overwrite, or the merge summary dict
            setConfirmState(null)
            setRestoringId(null)
            setConfirmAction(null)
            writePendingFile(null)
            if (j.result && Object.keys(j.result).length > 0) {
              const mr = j.result
              setNotice(
                `追記が完了しました: アイテム${mr.items_added}件追加` +
                (mr.items_skipped ? `(重複${mr.items_skipped}件はスキップ)` : '') +
                `、プレビュー画像${mr.previews_added}件、キャラクターグループ${mr.groups_added}件、` +
                `キャラ↔Danbooruリンク${mr.character_links_added ?? 0}件追加。ページを再読み込みしてください。`
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

  // react=true here (unlike backup's own mount-resume effect) so a reload
  // that lands exactly after a restore this session was tracking already
  // reached a terminal state (needs_confirmation, or a result/error that
  // finished while the page was reloading) still surfaces it, instead of
  // silently dropping it because nothing was polling yet to react to it.
  // This is safe from resurrecting truly-stale/already-handled state from
  // an unrelated earlier visit because pollRestoreStatus only acts on
  // needs_confirmation/error/result when readPendingFile() is non-null —
  // and that's cleared the moment any of those is actually handled.
  useEffect(() => {
    pollRestoreStatus(true).then(j => { if (j && j.running) startRestorePolling() })
    return () => { if (restorePollRef.current) clearInterval(restorePollRef.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function doRestore(file, mode) {
    writePendingFile(file)
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
      saveCachedStatus(RESTORE_STATUS_CACHE_KEY, j)
      startRestorePolling()
    } catch (e) {
      setError(e.message)
      setRestoringId(null)
      setConfirmAction(null)
      writePendingFile(null)
    }
  }

  function restoreBackup(file) {
    doRestore(file, 'strict')
  }

  function confirmOverwrite() {
    if (!confirmState) return
    setConfirmAction('overwrite')
    doRestore(confirmState.file, 'overwrite')
  }

  function confirmMerge() {
    if (!confirmState) return
    setConfirmAction('merge')
    doRestore(confirmState.file, 'merge')
  }

  function cancelOverwrite() {
    setConfirmState(null)
    setRestoringId(null)
    setConfirmAction(null)
    writePendingFile(null)
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
              {' '}
              <button
                className="btn"
                style={{ fontSize: 11, padding: '2px 8px', background: 'transparent', color: '#60a5fa' }}
                onClick={() => setShowGoogleCloudHelp(true)}
              >
                作成手順を見る
              </button>
            </div>
            {showGoogleCloudHelp && <GoogleCloudSetupHelp onClose={() => setShowGoogleCloudHelp(false)} />}
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
                <button className="btn" onClick={cancelOverwrite} disabled={!!confirmAction}>
                  キャンセル
                </button>
                <button
                  className="btn"
                  style={{ background: '#16a34a', borderColor: '#16a34a' }}
                  onClick={confirmMerge}
                  disabled={!!confirmAction}
                >
                  {confirmAction === 'merge' ? '処理中…' : '追記して復元'}
                </button>
                <button
                  className="btn"
                  style={{ background: '#ef4444', borderColor: '#ef4444' }}
                  onClick={confirmOverwrite}
                  disabled={!!confirmAction}
                >
                  {confirmAction === 'overwrite' ? '復元中…' : '上書きして復元'}
                </button>
              </div>
            </div>
          ) : (
          <>
          <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <button
              className="btn"
              onClick={createBackup}
              disabled={creating || backupStatus?.running || restoreStatus?.running}
            >
              {creating ? '開始しています…' : backupStatus?.running ? 'バックアップ中…' : '今すぐバックアップ'}
            </button>
            {/* Restore mutates the same tables a backup dump reads — the
                server refuses to run both at once (see backup_progress.py/
                restore_progress.py's cross-check); surfacing why here
                up-front, rather than only after a click produces a 409,
                is what actually makes the conflict legible instead of the
                button just silently doing nothing useful. */}
            {!backupStatus?.running && restoreStatus?.running && (
              <span style={{ fontSize: 12, color: '#94a3b8' }}>(復元処理が完了するまでバックアップはできません)</span>
            )}
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
            {backupStatus?.running && <> (バックアップ実行中は復元できません)</>}
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
                    disabled={restoringId != null || backupStatus?.running}
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
