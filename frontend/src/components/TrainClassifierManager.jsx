import React, { useState, useEffect, useRef, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function formatElapsed(startedAt) {
  if (!startedAt) return ''
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt))
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}分${s}秒`
}

// GUI front-end for item.classifier_training (see that module's docstring
// for the full "why a subprocess, why this shape" reasoning) — replaces
// exe/train.ps1 for the common case of "I added characters, let's
// retrain": a normal end user who just double-clicks the app to use it
// was never going to open PowerShell, remember CLI flags, or debug
// execution-policy/file-association errors to get there.
export default function TrainClassifierManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const [minImages, setMinImages] = useState('15')
  const [exclude, setExclude] = useState('')
  const [includeMulti, setIncludeMulti] = useState(false)
  const [backend, setBackend] = useState('onnx')

  const logRef = useRef(null)
  const pollRef = useRef(null)

  const poll = useCallback(async () => {
    try {
      const r = await fetch('/api/train_classifier/status/', { credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) { setError(j.detail || `状態の取得に失敗しました (${r.status})`); return }
      setStatus(j)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { poll() }, [poll])

  // Poll every 2s only while this panel is open — a run itself keeps
  // going in the background regardless (see classifier_training.py), so
  // closing/reopening the panel just resumes watching it, never loses it.
  useEffect(() => {
    pollRef.current = setInterval(poll, 2000)
    return () => clearInterval(pollRef.current)
  }, [poll])

  // Auto-scroll the log to the bottom as new lines arrive, but only if the
  // user was already at (or near) the bottom — so scrolling up to read an
  // earlier line doesn't get yanked back down on the next poll tick.
  useEffect(() => {
    const el = logRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    if (atBottom) el.scrollTop = el.scrollHeight
  }, [status?.log_tail])

  async function start() {
    setStarting(true)
    setError('')
    try {
      const options = {
        backend,
        min_images: minImages.trim() ? parseInt(minImages, 10) : undefined,
        exclude: exclude.trim(),
        include_multi_character: includeMulti,
      }
      const r = await fetch('/api/train_classifier/start/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify(options),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) { setError(j.detail || `開始に失敗しました (${r.status})`); return }
      setStatus(j)
    } catch (e) {
      setError(e.message)
    } finally {
      setStarting(false)
    }
  }

  const running = !!status?.running
  const finished = !running && status?.finished_at != null

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 640 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>キャラクター分類器の学習</strong>
          <button
            className="cgm-panel-close"
            onClick={onClose}
            title={finished && status.returncode === 0
              ? '学習が完了しています。このパネルを閉じた後、アプリ本体のウィンドウも閉じて fanart_viewer.exe をもう一度起動すると、新しいモデルが反映されます'
              : undefined}
          >✕</button>
        </div>

        <div className="cgm-panel-body">
          {status && !status.available ? (
            <div style={{ fontSize: 13, color: '#94a3b8' }}>
              この機能はビルド済みexe版でのみ利用できます。開発/Docker環境で学習する場合は
              RELEASE_LOCAL.mdの「AI提案パイプライン」節を参照してください。
            </div>
          ) : (
            <>
              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
                編集キューでタイトル・キャラクターを登録した画像を使って、キャラクター自動提案用の
                AIモデルを学習(または再学習)します。数分〜数時間かかることがあります。
                学習が終わったら、この画面を閉じてアプリを再起動すると新しいモデルが反映されます。
              </div>

              {error && <div style={{ color: '#f87171', marginBottom: 12, fontSize: 13 }}>{error}</div>}

              {!running && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: '#94a3b8' }}>
                      最低画像枚数(1キャラあたり)
                      <input
                        type="number" min={1} value={minImages} onChange={e => setMinImages(e.target.value)}
                        style={{ width: 90, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                          borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
                      />
                    </label>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: '#94a3b8' }}>
                      画像解析モデル
                      <select value={backend} onChange={e => setBackend(e.target.value)}
                        style={{ background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 8px', fontSize: 13 }}>
                        <option value="onnx">標準(軽量・高速・推奨)</option>
                        <option value="canary">2026年学習の最新モデル(重い)</option>
                      </select>
                    </label>
                  </div>

                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                    除外するキャラクター名(任意 — カンマ区切り。全員集合カットにまとめて付けた名前など、
                    1人のキャラクターを指さない名前があれば指定してください)
                  </label>
                  <input
                    type="text" value={exclude} onChange={e => setExclude(e.target.value)}
                    placeholder="例: 集合カットまとめ,その他の除外名"
                    style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                      borderRadius: 6, padding: '8px 10px', fontSize: 13, boxSizing: 'border-box', marginBottom: 10 }}
                  />

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cbd5e1', marginBottom: 14, cursor: 'pointer' }}>
                    <input type="checkbox" checked={includeMulti} onChange={e => setIncludeMulti(e.target.checked)} />
                    複数人が写っている画像も学習に混ぜる(領域ラベル付けキューでラベル済みのもの中心。件数が少ないうちは不要です)
                  </label>

                  <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '9px 20px', fontSize: 13, fontWeight: 600 }}
                    onClick={start} disabled={starting}>
                    {starting ? '開始しています…' : '学習を開始'}
                  </button>
                </div>
              )}

              {running && (
                <div style={{ fontSize: 13, color: '#93c5fd', marginBottom: 10 }}>
                  学習中… (経過 {formatElapsed(status.started_at)})
                  {status.args?.length > 0 && (
                    <span style={{ color: '#64748b', marginLeft: 8 }}>{status.args.join(' ')}</span>
                  )}
                </div>
              )}

              {finished && (
                <div style={{ fontSize: 13, marginBottom: 10, color: status.returncode === 0 ? '#4ade80' : '#f87171' }}>
                  {status.returncode === 0
                    ? '学習が正常に完了しました。反映するには、アプリのウィンドウを閉じて fanart_viewer.exe をもう一度起動してください(このパネルを閉じるだけでは反映されません)。'
                    : `学習がエラーで終了しました(終了コード ${status.returncode})。下のログを確認してください。`}
                </div>
              )}

              {(running || finished) && (
                <pre
                  ref={logRef}
                  style={{
                    background: '#0f172a', color: '#cbd5e1', fontSize: 11, lineHeight: 1.5,
                    padding: '10px 12px', borderRadius: 6, height: 260, overflowY: 'auto',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0,
                  }}
                >
                  {status.log_tail || 'ログを読み込み中…'}
                </pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
