import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

const PLATFORM_LABELS = { twitter: 'x.com', pixiv: 'pixiv.net', poipiku: 'poipiku.com' }

// Opens a real, visible browser window at the platform's own login page
// (see backend/item/browser_login.py) instead of asking the user to open
// DevTools and copy cookie values by hand — mirrors BackupManager.jsx's
// own Google Drive flow ("click authenticate -> a real browser opens ->
// the result is captured automatically"), just for a plain cookie session
// instead of an OAuth token exchange, since these sites have no OAuth
// redirect to hook into. `onApplied(status)` is called with the resulting
// *_creds.status() shape once capture succeeds, so the parent panel's own
// status box updates immediately without a full reload.
export default function BrowserLoginPanel({ platform, onApplied }) {
  const [status, setStatus] = useState(null) // {platform, started_at} from /browser_login/status/
  const [opening, setOpening] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/browser_login/status/', { credentials: 'same-origin' })
      if (r.ok) setStatus(await r.json())
    } catch (_) {}
  }, [])

  // Picks up an already-open login window on mount — e.g. this panel was
  // closed and reopened while the browser window (opened from here
  // earlier) is still sitting there waiting for the user to finish.
  useEffect(() => { load() }, [load])

  const isOpenForThisPlatform = status?.platform === platform
  const isOpenForOtherPlatform = !!status?.platform && !isOpenForThisPlatform

  async function open() {
    setOpening(true)
    setError('')
    try {
      const r = await fetch('/api/browser_login/open/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ platform }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `開けませんでした (${r.status})`)
      setStatus(j)
    } catch (e) {
      setError(e.message)
    } finally {
      setOpening(false)
    }
  }

  async function capture() {
    setCapturing(true)
    setError('')
    try {
      const r = await fetch('/api/browser_login/capture/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ platform }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `取得に失敗しました (${r.status})`)
      setStatus({ platform: null, started_at: null })
      if (onApplied) onApplied(j)
    } catch (e) {
      // The browser window is already closed server-side by this point
      // (see browser_login.py's worker: the cookie read + close happens
      // before the "were they sufficient?" check) — the error just means
      // login wasn't actually finished when "ログイン完了" was clicked, so
      // the button below goes back to "start over", not "try capture again".
      setStatus({ platform: null, started_at: null })
      setError(e.message)
    } finally {
      setCapturing(false)
    }
  }

  async function cancel() {
    setError('')
    try {
      await fetch('/api/browser_login/cancel/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ platform }),
      })
    } catch (_) {}
    setStatus({ platform: null, started_at: null })
  }

  return (
    <div style={{ marginBottom: 16, padding: '10px 12px', background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }}>
      <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
        推奨: 開発者ツールでCookieを手動コピーする代わりに、実際のブラウザで{PLATFORM_LABELS[platform]}に
        ログインするだけで、必要な情報をこちらで自動的に取得・保存できます。
      </div>
      {error && <div style={{ color: '#f87171', fontSize: 12, marginBottom: 8 }}>{error}</div>}
      {isOpenForThisPlatform ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#93c5fd' }}>
            別ウィンドウが開いています。ログインが終わったら「ログイン完了」を押してください。
          </span>
          <button className="btn" style={{ fontSize: 12 }} onClick={capture} disabled={capturing}>
            {capturing ? '取得中…' : 'ログイン完了'}
          </button>
          <button className="btn" style={{ fontSize: 12, background: '#334155' }} onClick={cancel} disabled={capturing}>
            キャンセル
          </button>
        </div>
      ) : (
        <button className="btn" style={{ fontSize: 12 }} onClick={open} disabled={opening || isOpenForOtherPlatform}>
          {opening ? '開いています…' : `ブラウザで${PLATFORM_LABELS[platform]}にログイン`}
        </button>
      )}
      {isOpenForOtherPlatform && (
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
          他の認証ウィンドウが開いています。先にそちらを完了/キャンセルしてください。
        </div>
      )}
    </div>
  )
}
