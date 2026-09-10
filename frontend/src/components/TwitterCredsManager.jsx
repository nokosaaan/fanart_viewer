import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ja-JP')
}

const SOURCE_LABELS = { db: 'このUIから保存済み', env: '.env (TWITTER_AUTH_TOKEN/CT0)', none: '未設定' }

// Admin-only panel to set the Twitter/X session cookies (auth_token/ct0)
// used by the scraping fetchers, replacing manual .env edits + container
// recreation. Deliberately write-only: there is no endpoint that returns the
// stored value, so this panel never fetches/shows the actual cookies —
// only whether something is configured and when it changed (see
// twitter_creds_views.py).
export default function TwitterCredsManager({ onClose }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [authToken, setAuthToken] = useState('')
  const [ct0, setCt0] = useState('')
  const [twid, setTwid] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pollStatus, setPollStatus] = useState(null)
  const [pollerEnabled, setPollerEnabled] = useState(false)
  const [pollerItemsPerTick, setPollerItemsPerTick] = useState(1)
  const [pollerIntervalValue, setPollerIntervalValue] = useState(6)
  const [pollerIntervalUnit, setPollerIntervalUnit] = useState('minutes')
  const [pollerBackfillPages, setPollerBackfillPages] = useState(3)
  const [pollerSaving, setPollerSaving] = useState(false)
  const [pollerNotice, setPollerNotice] = useState('')
  const [pollerError, setPollerError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/twitter_creds/status/', { credentials: 'same-origin' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `状態の取得に失敗しました (${r.status})`)
      setStatus(j)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }

    // Best-effort — the poller service may not be running in every
    // deployment, so a failure here shouldn't block the panel itself.
    try {
      const r2 = await fetch('/api/twitter_poll/status/', { credentials: 'same-origin' })
      if (r2.ok) setPollStatus(await r2.json())
    } catch (_) {}

    try {
      const r3 = await fetch('/api/poller_settings/status/', { credentials: 'same-origin' })
      if (r3.ok) {
        const j3 = await r3.json()
        setPollerEnabled(j3.enabled)
        setPollerItemsPerTick(j3.items_per_tick)
        setPollerIntervalValue(j3.interval_value)
        setPollerIntervalUnit(j3.interval_unit)
        setPollerBackfillPages(j3.backfill_pages_per_tick)
      }
    } catch (_) {}
  }, [])

  useEffect(() => { load() }, [load])

  async function savePollerSettings(next) {
    setPollerSaving(true)
    setPollerError('')
    setPollerNotice('')
    try {
      const r = await fetch('/api/poller_settings/set/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({
          enabled: next.enabled, items_per_tick: next.itemsPerTick,
          interval_value: next.intervalValue, interval_unit: next.intervalUnit,
          backfill_pages_per_tick: next.backfillPages,
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setPollerEnabled(j.enabled)
      setPollerItemsPerTick(j.items_per_tick)
      setPollerIntervalValue(j.interval_value)
      setPollerIntervalUnit(j.interval_unit)
      setPollerBackfillPages(j.backfill_pages_per_tick)
      setPollerNotice('保存しました。')
    } catch (e) {
      setPollerError(e.message)
    } finally {
      setPollerSaving(false)
    }
  }

  async function save() {
    if (!authToken.trim() || !ct0.trim()) { setError('auth_token と ct0 の両方を入力してください'); return }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch('/api/twitter_creds/set/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        // twidが空欄なら送らない — 空文字を送ると「消去」ではなく「変更なし」
        // として扱われる(twitter_creds.set_credentialsの仕様)ので実害は無いが、
        // 意図を明確にするため未入力時はキー自体を省く。
        body: JSON.stringify({
          auth_token: authToken.trim(), ct0: ct0.trim(),
          ...(twid.trim() ? { twid: twid.trim() } : {}),
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setStatus(j)
      setAuthToken('')
      setCt0('')
      setTwid('')
      setNotice('保存しました。次回のfetchから即座にこの認証情報が使われます(再起動不要)。')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>Twitter/X 認証情報</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body">
          {error && <div style={{ color: '#f87171', marginBottom: 12 }}>{error}</div>}
          {notice && <div style={{ color: '#4ade80', marginBottom: 12 }}>{notice}</div>}

          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            センシティブ/非公開アカウントの取得やRT・ブックマーク一括取得に使うx.comのセッションCookieです。
            ブラウザでx.comにログインした状態でDevTools → Application → Cookiesから
            <code style={{ margin: '0 4px' }}>auth_token</code>・<code style={{ margin: '0 4px' }}>ct0</code>・
            <code style={{ margin: '0 4px' }}>twid</code>をコピーしてください(twidは「いいね」自動取得のアカウント特定にのみ使用、ブックマーク取得には不要です)。
            保存した値はサーバー側で暗号化して保存され、この画面を含めどこにも読み出し表示はされません(書き込み専用)。
          </div>

          <div style={{ fontSize: 13, marginBottom: 16, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
            {loading ? '状態を確認中…' : status ? (
              <>現在の設定: <strong>{status.configured ? '設定済み' : '未設定'}</strong>
                {status.configured && <> ({SOURCE_LABELS[status.source] || status.source})</>}
                {status.updated_at && <> — 最終更新 {formatDate(status.updated_at)}</>}
                <br />twid: <strong>{status.has_twid ? '設定済み' : '未設定'}</strong>
                {!status.has_twid && <span style={{ color: '#94a3b8' }}> (いいね自動取得に必要。ブックマーク取得には不要)</span>}
              </>
            ) : '—'}
          </div>

          {pollStatus && (
            <div style={{ fontSize: 13, marginBottom: 16, padding: '8px 12px', background: '#0f172a', borderRadius: 6 }}>
              <div style={{ marginBottom: 4 }}>
                ブックマーク/いいね自動取得: 最終成功 {formatDate(pollStatus.last_success_at)}
                {' '}— 未処理キュー {pollStatus.pending_count}件
              </div>
              {pollStatus.consecutive_failures > 0 && (
                <div style={{ color: '#f87171' }}>
                  {pollStatus.consecutive_failures}回連続で失敗中 ({formatDate(pollStatus.last_error_at)}): {pollStatus.last_error}
                </div>
              )}
            </div>
          )}

          <div style={{ marginBottom: 20, padding: '12px', border: '1px solid #334155', borderRadius: 6 }}>
            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input
                  type="checkbox" checked={pollerEnabled}
                  onChange={e => {
                    const enabled = e.target.checked
                    setPollerEnabled(enabled)
                    savePollerSettings({
                      enabled, itemsPerTick: pollerItemsPerTick,
                      intervalValue: pollerIntervalValue, intervalUnit: pollerIntervalUnit,
                      backfillPages: pollerBackfillPages,
                    })
                  }}
                />
                <strong>自動でブックマーク/いいねを取得する(Twitter/Pixiv共通)</strong>
              </label>
              <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                オフの間は裏で一切取得を行いません。オンにすると下記の頻度・件数で、設定済みのTwitter/Pixiv両方について継続的に取得します(この設定は両方で共通です)。
              </div>
            </div>

            {pollerError && <div style={{ color: '#f87171', marginBottom: 8, fontSize: 13 }}>{pollerError}</div>}
            {pollerNotice && <div style={{ color: '#4ade80', marginBottom: 8, fontSize: 13 }}>{pollerNotice}</div>}

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <span style={{ fontSize: 13 }}>件数</span>
              <input
                type="number" min="1" value={pollerItemsPerTick}
                onChange={e => setPollerItemsPerTick(parseInt(e.target.value, 10) || 1)}
                style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
              />
              <span style={{ fontSize: 13 }}>件を</span>
              <input
                type="number" min="1" value={pollerIntervalValue}
                onChange={e => setPollerIntervalValue(parseInt(e.target.value, 10) || 1)}
                style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
              />
              <select
                value={pollerIntervalUnit} onChange={e => setPollerIntervalUnit(e.target.value)}
                style={{ background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
              >
                <option value="minutes">分</option>
                <option value="hours">時間</option>
                <option value="days">日</option>
                <option value="weeks">週</option>
              </select>
              <span style={{ fontSize: 13 }}>ごとに取得</span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <span style={{ fontSize: 13 }}>初回の遡り取得(バックフィル)は1回あたり</span>
              <input
                type="number" min="1" value={pollerBackfillPages}
                onChange={e => setPollerBackfillPages(parseInt(e.target.value, 10) || 1)}
                style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                  borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
              />
              <span style={{ fontSize: 13 }}>ページずつ(未処理の古いブックマーク/RT/いいねに追いつくまでの速さ。値を上げるほど早く最古まで到達しますが、1回あたりのリクエスト数が増えます)</span>
            </div>

            <button
              className="btn" style={{ fontSize: 13 }}
              disabled={pollerSaving}
              onClick={() => savePollerSettings({
                enabled: pollerEnabled, itemsPerTick: pollerItemsPerTick,
                intervalValue: pollerIntervalValue, intervalUnit: pollerIntervalUnit,
                backfillPages: pollerBackfillPages,
              })}
            >
              {pollerSaving ? '保存中…' : '頻度・件数を保存'}
            </button>
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>auth_token</label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={authToken} onChange={e => setAuthToken(e.target.value)}
              placeholder="新しい auth_token"
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>ct0</label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={ct0} onChange={e => setCt0(e.target.value)}
              placeholder="新しい ct0"
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
              twid(任意 — 未入力なら既存の設定を変更しません)
            </label>
            <input
              type="password" autoComplete="off"
              style={{ width: '100%', background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
                borderRadius: 6, padding: '9px 12px', fontSize: 14, boxSizing: 'border-box' }}
              value={twid} onChange={e => setTwid(e.target.value)}
              placeholder="新しい twid (例: u=1234567890)"
            />
          </div>

          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px', fontSize: 14, fontWeight: 600 }}
            onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
