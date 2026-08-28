import React, { useState } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

// Scans an account's timeline for retweets (see
// twitter_gql_fetch.fetch_account_retweets) in one of two modes:
// - auto: fire-and-forget background job that downloads+saves every new RT
//   it finds, for anyone who just wants images without reviewing each one.
// - manual (default): synchronously fetches candidate metadata only (no
//   download yet) so the user picks exactly which tweets to actually import
//   — added because auto-saving whatever the scan turned up was judged too
//   heavy-handed; image selection should stay a deliberate, reviewed step.
export default function RetweetFetchManager({ onClose }) {
  const [mode, setMode] = useState('manual')
  const [screenName, setScreenName] = useState('')
  const [maxItems, setMaxItems] = useState(30)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // manual-mode review state
  const [candidates, setCandidates] = useState(null) // null = not searched yet
  const [selected, setSelected] = useState(() => new Set())
  const [alreadyArchived, setAlreadyArchived] = useState(0)
  const [importing, setImporting] = useState(false)

  function resetResults() {
    setCandidates(null)
    setSelected(new Set())
    setAlreadyArchived(0)
  }

  async function runAuto() {
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

  async function runSearch() {
    const name = screenName.trim().replace(/^@/, '')
    if (!name) { setError('アカウント名を入力してください'); return }
    setSubmitting(true)
    setError('')
    setNotice('')
    resetResults()
    try {
      const r = await fetch('/api/items/preview_account_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ screen_name: name, max_items: maxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      setCandidates(j.candidates || [])
      setSelected(new Set((j.candidates || []).map(c => c.tweet_id)))
      setAlreadyArchived(j.already_archived || 0)
      if ((j.candidates || []).length === 0) {
        setNotice(j.already_archived > 0 ? '新規のRTは見つかりませんでした(すべて登録済みです)。' : 'RTが見つかりませんでした。')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  function toggle(tweetId) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(tweetId)) next.delete(tweetId); else next.add(tweetId)
      return next
    })
  }

  async function runImport() {
    const chosen = (candidates || []).filter(c => selected.has(c.tweet_id))
    if (chosen.length === 0) { setError('インポートする項目を選択してください'); return }
    setImporting(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/items/import_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ retweets: chosen }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `インポートに失敗しました (${r.status})`)
      setNotice(`${j.created}件登録しました（重複スキップ ${j.skipped}件、失敗 ${j.failed}件）。`)
      resetResults()
    } catch (e) {
      setError(e.message)
    } finally {
      setImporting(false)
    }
  }

  const showReview = mode === 'manual' && candidates !== null && candidates.length > 0

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={showReview ? { width: 720 } : undefined} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>アカウントのRTをまとめて取得</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" checked={mode === 'manual'} onChange={() => { setMode('manual'); resetResults() }} />
              候補を確認してから選ぶ(推奨)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" checked={mode === 'auto'} onChange={() => { setMode('auto'); resetResults() }} />
              自動で取得・保存(お任せ)
            </label>
          </div>

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            {mode === 'manual'
              ? '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTの候補一覧を取得します。画像はまだダウンロードされません — 一覧から取り込みたいものだけ選んでインポートしてください。'
              : '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTを新しい順に集めて自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
            {' '}レート制限保護のため一度に取得する件数には上限があります。
          </div>

          {!showReview && (
            <>
              <div style={{ marginBottom: 14 }}>
                <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>アカウント (@なし)</label>
                <input
                  style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                    borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
                  value={screenName} onChange={e => setScreenName(e.target.value)}
                  placeholder="screen_name"
                  onKeyDown={e => { if (e.key === 'Enter') (mode === 'manual' ? runSearch() : runAuto()) }}
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
                onClick={mode === 'manual' ? runSearch : runAuto} disabled={submitting}>
                {submitting ? (mode === 'manual' ? '検索中…' : '開始中…') : (mode === 'manual' ? '候補を検索' : '取得を開始')}
              </button>
            </>
          )}

          {showReview && (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <span style={{ fontSize: 13, color: '#94a3b8' }}>
                  {candidates.length}件の候補{alreadyArchived > 0 && `(登録済み${alreadyArchived}件は除外済み)`}
                </span>
                <button className="btn" style={{ fontSize: 12 }} onClick={resetResults}>やり直す</button>
              </div>

              <div style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid #334155', borderRadius: 6, marginBottom: 14 }}>
                {candidates.map(c => (
                  <label key={c.tweet_id}
                    style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px',
                      borderBottom: '1px solid #1e293b', cursor: 'pointer' }}>
                    <input type="checkbox" checked={selected.has(c.tweet_id)} onChange={() => toggle(c.tweet_id)}
                      style={{ marginTop: 4 }} />
                    {c.media_urls[0] && (
                      <img src={c.media_urls[0]} alt="" style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: 4, flexShrink: 0 }} />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>
                        {c.screen_name ? `@${c.screen_name}` : <span style={{ color: '#f59e0b' }}>(作者名不明)</span>}
                        <span style={{ marginLeft: 8, fontSize: 11, color: '#64748b' }}>画像{c.media_urls.length}枚</span>
                      </div>
                      {c.description && <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>{truncate(c.description, 80)}</div>}
                      <a href={c.link} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: '#60a5fa' }}>元ツイートを開く</a>
                    </div>
                  </label>
                ))}
              </div>

              <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                onClick={runImport} disabled={importing || selected.size === 0}>
                {importing ? 'インポート中…' : `選択した${selected.size}件をインポート`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
