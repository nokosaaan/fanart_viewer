import React, { useState } from 'react'
import { fetchPreviewCandidates, sleep, BULK_FETCH_DELAY_MS } from '../lib/fetchCandidates'
import { notify } from '../lib/crossWindowSync'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Manual, on-demand counterpart to poll_twitter_updates.py's own automatic
// per-tick bookmark discovery — same fetch_account_bookmarks call, same
// two modes as RetweetFetchManager.jsx (queue / auto), just for the
// logged-in account's own bookmarks (session-based via the stored auth_
// token/ct0 — no account name to type in, unlike RTs which are scraped
// from a chosen account's public timeline). Most useful as an immediate
// "catch up now" when the poller itself has been unable to run for a
// while (e.g. an auth failure — see TwitterCredsManager's pollStatus) and
// a backlog of bookmarks has piled up unprocessed.
export default function BookmarkFetchManager({ onClose, onEnqueueFetch }) {
  const [mode, setMode] = useState('queue')
  const [maxPages, setMaxPages] = useState(5)
  const [fullScan, setFullScan] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [progress, setProgress] = useState(null) // {done, total}

  async function runAuto() {
    setSubmitting(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_bookmarks/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: maxPages, full_scan: fullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setNotice(`ブックマーク取得をバックグラウンドで開始しました（最大${maxPages}ページ）。完了しても通知は出ないので、しばらくしてから一覧を再読み込みしてください。`)
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function runQueue() {
    setSubmitting(true)
    setError('')
    setNotice('')
    setProgress(null)
    try {
      const r = await fetch('/api/items/scan_account_bookmarks/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: maxPages, full_scan: fullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      const newItems = j.items || []
      if (newItems.length === 0) {
        setNotice(j.already_archived > 0 ? '新規のブックマークは見つかりませんでした(すべて登録済みです)。' : 'ブックマークが見つかりませんでした。')
        return
      }

      // Same per-item fetch-then-enqueue loop as RetweetFetchManager's
      // queue mode / FetchQueueManager's page bulk-fetch button.
      let queued = 0, savedDirect = 0, failed = 0
      for (let i = 0; i < newItems.length; i++) {
        // Space out requests — see BULK_FETCH_DELAY_MS's own comment:
        // firing these back-to-back with no gap has been observed to trip
        // Twitter's rate limit and fail every item in the batch.
        if (i > 0) await sleep(BULK_FETCH_DELAY_MS)
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
          console.error('Fetch failed for bookmark item', it.id, e)
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
          <strong>ブックマークをまとめて取得</strong>
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
              ? 'ログイン中アカウントのブックマークを新しい順にさかのぼり、まだ登録していないものごとにアイテムを作成し、通常のリンクfetchと同じ流れで画像候補を取得キューに追加します。画像の選定は取得キューからいつも通り行えます。'
              : 'ログイン中アカウントのブックマークを新しい順にさかのぼり、新規のものを自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
            {' '}アカウント名の指定は不要です(保存済みのTwitter/X認証情報でログイン中のアカウント自身のブックマークを見ます)。ポーリングが認証エラー等でしばらく動いていなかった場合の追いつき用にどうぞ。自動ポーリングと同じ「どこまで見たか」の位置を共有しているので、既にポーリングが追いついている範囲は再スキャンせず、その続き(より古い分)から取得します — 実行後「見つかりませんでした」と出た場合は、既に自動ポーリングが追いついている(新しいブックマークが無い)ことを意味します。
          </div>

          <div style={{ marginBottom: 16, padding: '10px 12px', background: '#0f172a', borderRadius: 6 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
              <input type="checkbox" checked={fullScan} onChange={e => setFullScan(e.target.checked)} />
              <strong>完全スキャン(抜け漏れも探す)</strong>
            </label>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
              通常は「登録済みのツイートに1件でも当たった時点」で新しい方から遡るのを打ち切ります(効率重視)。一時的な不具合等で一部だけ取り込みそびれた「抜け」がある場合、通常モードではその抜けより新しい/古いに関わらず永久に見つかりません。このチェックを入れると、登録済みのツイートに当たっても打ち切らずスキップして先(より古い方)まで探し続けます — 抜けの回復用。時間がかかるので、上記のページ数上限は少し多め(10〜20)がおすすめです。
            </div>
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
              取得ページ数の上限(1ページ約20件、目安: 5〜10ページ程度が安全)
            </label>
            <input
              type="number" min={1} max={20}
              style={{ width: 120, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={maxPages} onChange={e => setMaxPages(Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 1)))}
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
