import React, { useState, useEffect } from 'react'
import { fetchPreviewCandidates, sleep, BULK_FETCH_DELAY_MS } from '../lib/fetchCandidates'
import { notify } from '../lib/crossWindowSync'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

const PLATFORMS = [
  { key: 'bookmark', label: 'ブックマーク' },
  { key: 'like', label: 'いいね' },
  { key: 'retweet', label: 'RT' },
]

// Single entry point ("Twitterから画像取得") branching into the three kinds
// of Twitter/X bulk fetch this app supports, replacing what used to be
// three separate header-menu items/modals (BookmarkFetchManager.jsx,
// LikeFetchManager.jsx, RetweetFetchManager.jsx — now folded in here).
// Each tab keeps its own previously-independent state/logic (they still
// hit different endpoints with different requirements) — only the outer
// shell (which panel is showing) is shared, via the `platform` tab.
//
// What each kind actually needs, shown right under the tabs:
// - ブックマーク: only the stored Twitter/X credentials (auth_token/ct0) —
//   it's always the logged-in account's own bookmarks, no account name.
// - いいね: same credentials, PLUS a `twid` cookie (to resolve which
//   account "the logged-in account" even is) — the one extra requirement
//   bookmarks doesn't have, so it's called out explicitly here.
// - RT: credentials, PLUS an account name to type in (RTs are scraped
//   from a chosen account's own public timeline, not the logged-in
//   account's own activity).
export default function TwitterFetchManager({ onClose, onEnqueueFetch }) {
  const [platform, setPlatform] = useState('bookmark')
  const [hasTwid, setHasTwid] = useState(null) // null = not checked yet

  useEffect(() => {
    fetch('/api/twitter_creds/status/', { credentials: 'same-origin' })
      .then(r => r.json()).then(j => setHasTwid(!!j.has_twid)).catch(() => {})
  }, [])

  // --- ブックマーク --------------------------------------------------
  const [bmMode, setBmMode] = useState('queue')
  const [bmMaxPages, setBmMaxPages] = useState(5)
  const [bmFullScan, setBmFullScan] = useState(false)
  const [bmSubmitting, setBmSubmitting] = useState(false)
  const [bmError, setBmError] = useState('')
  const [bmNotice, setBmNotice] = useState('')
  const [bmProgress, setBmProgress] = useState(null)

  const [bmMaxItems, setBmMaxItems] = useState(20000)
  const [bmBacklogSubmitting, setBmBacklogSubmitting] = useState(false)
  const [bmBacklogError, setBmBacklogError] = useState('')
  const [bmBacklogNotice, setBmBacklogNotice] = useState('')

  async function bmRunAuto() {
    setBmSubmitting(true); setBmError(''); setBmNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_bookmarks/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: bmMaxPages, full_scan: bmFullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setBmNotice(`ブックマーク取得をバックグラウンドで開始しました（最大${bmMaxPages}ページ）。完了しても通知は出ないので、しばらくしてから一覧を再読み込みしてください。`)
    } catch (e) {
      setBmError(e.message)
    } finally {
      setBmSubmitting(false)
    }
  }

  async function bmRunQueue() {
    setBmSubmitting(true); setBmError(''); setBmNotice(''); setBmProgress(null)
    try {
      const r = await fetch('/api/items/scan_account_bookmarks/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: bmMaxPages, full_scan: bmFullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      const newItems = j.items || []
      if (newItems.length === 0) {
        setBmNotice(j.already_archived > 0 ? '新規のブックマークは見つかりませんでした(すべて登録済みです)。' : 'ブックマークが見つかりませんでした。')
        return
      }
      let queued = 0, savedDirect = 0, failed = 0
      for (let i = 0; i < newItems.length; i++) {
        if (i > 0) await sleep(BULK_FETCH_DELAY_MS)
        setBmProgress({ done: i, total: newItems.length })
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
      setBmProgress({ done: newItems.length, total: newItems.length })
      setBmNotice(`新規${newItems.length}件(登録済み${j.already_archived}件は除外)を処理しました — 取得キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件。「取得キュー」から画像を選んでください。`)
    } catch (e) {
      setBmError(e.message)
    } finally {
      setBmSubmitting(false)
    }
  }

  async function bmRunBacklog() {
    setBmBacklogSubmitting(true); setBmBacklogError(''); setBmBacklogNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_backlog_bookmarks/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_items: bmMaxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setBmBacklogNotice(`バックグラウンドで開始しました（最大${bmMaxItems}件チェック）。数十分〜数時間かかることがあります。「Twitter/X 認証情報」パネルの「未処理キュー」件数が増えていけば動作中の合図です — 見つかった分は自動ポーリングが少しずつ取得していきます（新しいブックマークが優先されるので、通常運用の妨げにはなりません）。`)
    } catch (e) {
      setBmBacklogError(e.message)
    } finally {
      setBmBacklogSubmitting(false)
    }
  }

  // --- いいね ----------------------------------------------------------
  const [likeMode, setLikeMode] = useState('queue')
  const [likeMaxPages, setLikeMaxPages] = useState(5)
  const [likeFullScan, setLikeFullScan] = useState(false)
  const [likeSubmitting, setLikeSubmitting] = useState(false)
  const [likeError, setLikeError] = useState('')
  const [likeNotice, setLikeNotice] = useState('')
  const [likeProgress, setLikeProgress] = useState(null)

  async function likeRunAuto() {
    setLikeSubmitting(true); setLikeError(''); setLikeNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_likes/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: likeMaxPages, full_scan: likeFullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setLikeNotice(`いいね取得をバックグラウンドで開始しました（最大${likeMaxPages}ページ）。完了しても通知は出ないので、しばらくしてから一覧を再読み込みしてください。`)
    } catch (e) {
      setLikeError(e.message)
    } finally {
      setLikeSubmitting(false)
    }
  }

  async function likeRunQueue() {
    setLikeSubmitting(true); setLikeError(''); setLikeNotice(''); setLikeProgress(null)
    try {
      const r = await fetch('/api/items/scan_account_likes/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ max_pages: likeMaxPages, full_scan: likeFullScan }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      const newItems = j.items || []
      if (newItems.length === 0) {
        setLikeNotice(j.already_archived > 0 ? '新規のいいねは見つかりませんでした(すべて登録済みです)。' : 'いいねが見つかりませんでした。')
        return
      }
      let queued = 0, savedDirect = 0, failed = 0
      for (let i = 0; i < newItems.length; i++) {
        if (i > 0) await sleep(BULK_FETCH_DELAY_MS)
        setLikeProgress({ done: i, total: newItems.length })
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
          console.error('Fetch failed for liked tweet item', it.id, e)
          failed++
        }
      }
      setLikeProgress({ done: newItems.length, total: newItems.length })
      setLikeNotice(`新規${newItems.length}件(登録済み${j.already_archived}件は除外)を処理しました — 取得キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件。「取得キュー」から画像を選んでください。`)
    } catch (e) {
      setLikeError(e.message)
    } finally {
      setLikeSubmitting(false)
    }
  }

  // --- RT ----------------------------------------------------------
  const [rtMode, setRtMode] = useState('queue')
  const [rtScreenName, setRtScreenName] = useState('')
  const [rtMaxItems, setRtMaxItems] = useState(30)
  const [rtSubmitting, setRtSubmitting] = useState(false)
  const [rtError, setRtError] = useState('')
  const [rtNotice, setRtNotice] = useState('')
  const [rtProgress, setRtProgress] = useState(null)

  async function rtRunAuto() {
    const name = rtScreenName.trim().replace(/^@/, '')
    if (!name) { setRtError('アカウント名を入力してください'); return }
    setRtSubmitting(true); setRtError(''); setRtNotice('')
    try {
      const r = await fetch('/api/items/fetch_account_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ screen_name: name, max_items: rtMaxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開始に失敗しました (${r.status})`)
      setRtNotice(`@${name} のRT取得をバックグラウンドで開始しました（最大${rtMaxItems}件）。完了しても通知は出ないので、しばらくしてから一覧を再読み込みしてください。`)
    } catch (e) {
      setRtError(e.message)
    } finally {
      setRtSubmitting(false)
    }
  }

  async function rtRunQueue() {
    const name = rtScreenName.trim().replace(/^@/, '')
    if (!name) { setRtError('アカウント名を入力してください'); return }
    setRtSubmitting(true); setRtError(''); setRtNotice(''); setRtProgress(null)
    try {
      const r = await fetch('/api/items/scan_account_retweets/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ screen_name: name, max_items: rtMaxItems }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `検索に失敗しました (${r.status})`)
      const newItems = j.items || []
      if (newItems.length === 0) {
        setRtNotice(j.already_archived > 0 ? '新規のRTは見つかりませんでした(すべて登録済みです)。' : 'RTが見つかりませんでした。')
        return
      }
      let queued = 0, savedDirect = 0, failed = 0
      for (let i = 0; i < newItems.length; i++) {
        if (i > 0) await sleep(BULK_FETCH_DELAY_MS)
        setRtProgress({ done: i, total: newItems.length })
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
      setRtProgress({ done: newItems.length, total: newItems.length })
      setRtNotice(`新規${newItems.length}件(登録済み${j.already_archived}件は除外)を処理しました — 取得キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件。「取得キュー」から画像を選んでください。`)
    } catch (e) {
      setRtError(e.message)
    } finally {
      setRtSubmitting(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>Twitterから画像取得</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: 'flex', gap: 6, padding: '0 14px 10px' }}>
          {PLATFORMS.map(p => (
            <button key={p.key} className="btn" onClick={() => setPlatform(p.key)}
              style={{ fontSize: 13, background: platform === p.key ? '#2563eb' : '#334155', color: '#f1f5f9' }}>
              {p.label}
            </button>
          ))}
        </div>

        <div className="cgm-panel-body">
          {platform === 'bookmark' && (
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 12, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
              必要なもの: 保存済みのTwitter/X認証情報（<code>auth_token</code>/<code>ct0</code>）のみ。アカウント名の指定は不要です — ログイン中のアカウント自身のブックマークを見ます。
            </div>
          )}
          {platform === 'like' && (
            <div style={{ fontSize: 12, color: hasTwid === false ? '#fde68a' : '#94a3b8', marginBottom: 12, padding: '8px 12px',
              background: hasTwid === false ? '#78350f' : '#0f172a', borderRadius: 6 }}>
              必要なもの: 保存済みのTwitter/X認証情報に加えて<strong>twid Cookie</strong>（ログイン中アカウント自身の特定に使用）。
              {hasTwid === false && ' ⚠ 現在twidが未設定です — 「Twitter/X 認証情報」パネルから設定してください。'}
              {hasTwid === true && ' ✓ twid設定済みです。'}
            </div>
          )}
          {platform === 'retweet' && (
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 12, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
              必要なもの: 保存済みのTwitter/X認証情報に加えて、スキャンしたい<strong>アカウント名</strong>（ログイン中アカウント自身とは限りません — 他アカウントの公開タイムラインをスキャンします）。
            </div>
          )}

          {/* ============================= ブックマーク ============================= */}
          {platform === 'bookmark' && (
            <>
              {bmError && <div style={{ color: '#f87171', marginBottom: 12 }}>{bmError}</div>}
              {bmNotice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{bmNotice}</div>}

              <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={bmMode === 'queue'} onChange={() => setBmMode('queue')} />
                  取得キューに入れて選ぶ(推奨)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={bmMode === 'auto'} onChange={() => setBmMode('auto')} />
                  自動で取得・保存(お任せ)
                </label>
              </div>

              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
                {bmMode === 'queue'
                  ? 'ログイン中アカウントのブックマークを新しい順にさかのぼり、まだ登録していないものごとにアイテムを作成し、通常のリンクfetchと同じ流れで画像候補を取得キューに追加します。画像の選定は取得キューからいつも通り行えます。'
                  : 'ログイン中アカウントのブックマークを新しい順にさかのぼり、新規のものを自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
                {' '}ポーリングが認証エラー等でしばらく動いていなかった場合の追いつき用にどうぞ。自動ポーリングと同じ「どこまで見たか」の位置を共有しているので、既にポーリングが追いついている範囲は再スキャンせず、その続き(より古い分)から取得します — 実行後「見つかりませんでした」と出た場合は、既に自動ポーリングが追いついている(新しいブックマークが無い)ことを意味します。
              </div>

              <div style={{ marginBottom: 16, padding: '10px 12px', background: '#0f172a', borderRadius: 6 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                  <input type="checkbox" checked={bmFullScan} onChange={e => setBmFullScan(e.target.checked)} />
                  <strong>完全スキャン(抜け漏れも探す)</strong>
                </label>
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                  通常は「登録済みのツイートに1件でも当たった時点」で新しい方から遡るのを打ち切ります(効率重視)。一時的な不具合等で一部だけ取り込みそびれた「抜け」がある場合、通常モードではその抜けより新しい/古いに関わらず永久に見つかりません。このチェックを入れると、登録済みのツイートに当たっても打ち切らずスキップして先(より古い方)まで探し続けます — 抜けの回復用。時間がかかるので、下記のページ数上限は少し多め(10〜20)がおすすめです。
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
                  value={bmMaxPages} onChange={e => setBmMaxPages(Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 1)))}
                />
              </div>

              <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                onClick={bmMode === 'queue' ? bmRunQueue : bmRunAuto} disabled={bmSubmitting}>
                {bmSubmitting
                  ? (bmMode === 'queue' ? `処理中… (${bmProgress ? bmProgress.done : 0}/${bmProgress ? bmProgress.total : 0})` : '開始中…')
                  : (bmMode === 'queue' ? '取得してキューに追加' : '取得を開始')}
              </button>

              <hr style={{ border: 'none', borderTop: '1px solid #334155', margin: '24px 0' }} />

              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>バックログを一括取得(最大1000ページ=2万件)</div>
              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
                上とは別の、大規模な過去分の取り込み専用の機能です。現在のブックマーク状況を新しい方から指定件数までまとめて読み込み（打ち切らず読み切る — 「完全スキャン」と同じ考え方をさらに大きい規模で）、見つかった新規分をそのまま自動ポーリングと同じ待ち行列（取得キュー）に積みます。<strong>実際の画像取得は行わず、発見して積むだけ</strong>です — 実際の取得は自動ポーリングが少しずつ進めます。件数が多いとページ数もその分増え、Twitter側のレート制限との兼ね合いで数十分〜数時間かかることがあるため、必ずバックグラウンドで実行されます。自動ポーリングが見つける本当に新しいブックマークは常にこのバックログより優先して処理されるので（Twitter自身のツイートID＝作成順で降順に処理するため）、通常運用と競合する心配はありません。
              </div>

              {bmBacklogError && <div style={{ color: '#f87171', marginBottom: 12, fontSize: 13 }}>{bmBacklogError}</div>}
              {bmBacklogNotice && <div style={{ color: '#4ade80', marginBottom: 12, fontSize: 13 }}>{bmBacklogNotice}</div>}

              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                  チェックする件数の上限(1ページ約20件。目安: 1万件で数十分〜数時間)
                </label>
                <input
                  type="number" min={20} max={20000}
                  style={{ width: 140, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                    borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
                  value={bmMaxItems} onChange={e => setBmMaxItems(Math.max(20, Math.min(20000, parseInt(e.target.value, 10) || 20)))}
                />
              </div>

              <button className="btn" style={{ background: '#475569', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                onClick={bmRunBacklog} disabled={bmBacklogSubmitting}>
                {bmBacklogSubmitting ? '開始中…' : 'バックグラウンドで開始'}
              </button>
            </>
          )}

          {/* ============================= いいね ============================= */}
          {platform === 'like' && (
            <>
              {likeError && <div style={{ color: '#f87171', marginBottom: 12 }}>{likeError}</div>}
              {likeNotice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{likeNotice}</div>}

              <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={likeMode === 'queue'} onChange={() => setLikeMode('queue')} />
                  取得キューに入れて選ぶ(推奨)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={likeMode === 'auto'} onChange={() => setLikeMode('auto')} />
                  自動で取得・保存(お任せ)
                </label>
              </div>

              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
                {likeMode === 'queue'
                  ? 'ログイン中アカウントのいいねを新しい順にさかのぼり、まだ登録していないものごとにアイテムを作成し、通常のリンクfetchと同じ流れで画像候補を取得キューに追加します。画像の選定は取得キューからいつも通り行えます。'
                  : 'ログイン中アカウントのいいねを新しい順にさかのぼり、新規のものを自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
                {' '}いいねは自動ポーリングの対象外なので、新しいものを確認したい時にこのボタンから都度取得してください。前回どこまで見たかは覚えているので、続きから取得します。
              </div>

              <div style={{ marginBottom: 16, padding: '10px 12px', background: '#0f172a', borderRadius: 6 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                  <input type="checkbox" checked={likeFullScan} onChange={e => setLikeFullScan(e.target.checked)} />
                  <strong>完全スキャン(抜け漏れも探す)</strong>
                </label>
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                  通常は「登録済みのツイートに1件でも当たった時点」で新しい方から遡るのを打ち切ります。一時的な不具合等で一部だけ取り込みそびれた「抜け」がある場合、このチェックを入れると打ち切らずスキップして先まで探し続けます。時間がかかるので、下記のページ数上限は少し多め(10〜20)がおすすめです。
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
                  value={likeMaxPages} onChange={e => setLikeMaxPages(Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 1)))}
                />
              </div>

              <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                onClick={likeMode === 'queue' ? likeRunQueue : likeRunAuto} disabled={likeSubmitting}>
                {likeSubmitting
                  ? (likeMode === 'queue' ? `処理中… (${likeProgress ? likeProgress.done : 0}/${likeProgress ? likeProgress.total : 0})` : '開始中…')
                  : (likeMode === 'queue' ? '取得してキューに追加' : '取得を開始')}
              </button>
            </>
          )}

          {/* ============================= RT ============================= */}
          {platform === 'retweet' && (
            <>
              {rtError && <div style={{ color: '#f87171', marginBottom: 12 }}>{rtError}</div>}
              {rtNotice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{rtNotice}</div>}

              <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={rtMode === 'queue'} onChange={() => setRtMode('queue')} />
                  取得キューに入れて選ぶ(推奨)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" checked={rtMode === 'auto'} onChange={() => setRtMode('auto')} />
                  自動で取得・保存(お任せ)
                </label>
              </div>

              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
                {rtMode === 'queue'
                  ? '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTごとにアイテムを作成し、通常のリンクfetchと同じ流れで画像候補を取得キューに追加します。画像の選定は取得キューからいつも通り行えます。'
                  : '指定アカウントのタイムラインをさかのぼり、(引用ではない)RTを新しい順に集めて自動でアイテム登録します。既に登録済みのツイートはスキップされます。'}
                {' '}レート制限保護のため一度に取得する件数には上限があります。
              </div>

              <div style={{ marginBottom: 14 }}>
                <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>アカウント (@なし)</label>
                <input
                  style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                    borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
                  value={rtScreenName} onChange={e => setRtScreenName(e.target.value)}
                  placeholder="screen_name"
                  onKeyDown={e => { if (e.key === 'Enter') (rtMode === 'queue' ? rtRunQueue() : rtRunAuto()) }}
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
                  value={rtMaxItems} onChange={e => setRtMaxItems(Math.max(1, Math.min(100, parseInt(e.target.value, 10) || 1)))}
                />
              </div>

              <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
                onClick={rtMode === 'queue' ? rtRunQueue : rtRunAuto} disabled={rtSubmitting}>
                {rtSubmitting
                  ? (rtMode === 'queue' ? `処理中… (${rtProgress ? rtProgress.done : 0}/${rtProgress ? rtProgress.total : 0})` : '開始中…')
                  : (rtMode === 'queue' ? '取得してキューに追加' : '取得を開始')}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
