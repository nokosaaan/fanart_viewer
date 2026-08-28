import React, { useState } from 'react'
import { fetchPreviewCandidates } from '../lib/fetchCandidates'
import { notify } from '../lib/crossWindowSync'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Scans an account's timeline for retweets (see
// twitter_gql_fetch.fetch_account_retweets) in one of two modes:
// - queue (default): creates a bare Item (no preview yet) for each new RT,
//   then runs each one through the exact same fetchPreviewCandidates +
//   fetch-queue flow as any other link (see FetchQueueManager.runBulkFetch)
//   — so image selection for RT-derived items goes through the same
//   reviewed mailbox as everywhere else in the app, not a bespoke path.
// - auto: fire-and-forget background job that downloads+saves every new RT
//   it finds, for anyone who just wants images without reviewing each one.
export default function RetweetFetchManager({ onClose, onEnqueueFetch }) {
  const [mode, setMode] = useState('queue')
  const [screenName, setScreenName] = useState('')
  const [maxItems, setMaxItems] = useState(30)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [progress, setProgress] = useState(null) // {done, total}

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

  async function runQueue() {
    const name = screenName.trim().replace(/^@/, '')
    if (!name) { setError('アカウント名を入力してください'); return }
    setSubmitting(true)
    setError('')
    setNotice('')
    setProgress(null)
    try {
      const r = await fetch('/api/items/scan_account_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ screen_name: name, max_items: maxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      const newItems = j.items || []
      if (newItems.length === 0) {
        setNotice(j.already_archived > 0 ? '新規のRTは見つかりませんでした(すべて登録済みです)。' : 'RTが見つかりませんでした。')
        return
      }

      // Same per-item fetch-then-enqueue loop as FetchQueueManager's page
      // bulk-fetch button, just applied to the freshly created RT items
      // instead of currentPageItems.
      let queued = 0, savedDirect = 0, failed = 0
      for (let i = 0; i < newItems.length; i++) {
        setProgress({ done: i, total: newItems.length })
        const it = newItems[i]
        try {
          const res = await fetchPreviewCandidates(it.id, it.link)
          const body = res.body || {}
          if (res.ok && body.status === 'saved') {
            savedDirect++
            notify('item-preview-updated', { id: it.id })
          } else if (res.ok && body.preview_only && Array.isArray(body.images) && body.images.length > 0) {
            onEnqueueFetch({ itemId: it.id, images: body.images })
            queued++
          } else {
            failed++
          }
        } catch (e) {
          console.error('Fetch failed for RT item', it.id, e)
          failed++
        }
      }
      setProgress({ done: newItems.length, total: newItems.length })
      setNotice(`新規${newItems.length}件(登録済み${j.already_archived}件は除外)を処理しました — 取得キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件。「取得キュー」から画像を選んでください。`)
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>アカウントのRTをまとめて取得</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" checked={mode === 'queue'} onChange={() => setMode('queue')} />
              取得キューに入れて選ぶ(推奨)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" checked={mode === 'auto'} onChange={() => setMode('auto')} />
              自動で取得・保存(お任せ)
            </label>
          </div>

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            {mode === 'queue'
              ? '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTごとにアイテムを作成し、通常のリンクfetchと同じ流れで画像候補を取得キューに追加します。画像の選定は取得キューからいつも通り行えます。'
              : '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTを新しい順に集めて自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
            {' '}レート制限保護のため一度に取得する件数には上限があります。
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>アカウント (@なし)</label>
            <input
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={screenName} onChange={e => setScreenName(e.target.value)}
              placeholder="screen_name"
              onKeyDown={e => { if (e.key === 'Enter') (mode === 'queue' ? runQueue() : runAuto()) }}
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
            onClick={mode === 'queue' ? runQueue : runAuto} disabled={submitting}>
            {submitting
              ? (mode === 'queue' ? `処理中… (${progress ? progress.done : 0}/${progress ? progress.total : 0})` : '開始中…')
              : (mode === 'queue' ? '取得してキューに追加' : '取得を開始')}
          </button>
        </div>
      </div>
    </div>
  )
}
