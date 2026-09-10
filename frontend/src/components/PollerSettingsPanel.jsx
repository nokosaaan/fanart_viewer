import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Reusable poller on/off + rate-control block, one instance per platform
// (see item.models.PollerSettings — Twitter and Pixiv each get their own
// independent row/interval now, no longer a single shared setting).
// Used inside TwitterCredsManager.jsx (platform="twitter") and
// PixivCredsManager.jsx (platform="pixiv").
export default function PollerSettingsPanel({ platform, label }) {
  const [enabled, setEnabled] = useState(false)
  const [itemsPerTick, setItemsPerTick] = useState(1)
  const [intervalValue, setIntervalValue] = useState(6)
  const [intervalUnit, setIntervalUnit] = useState('minutes')
  const [backfillPages, setBackfillPages] = useState(3)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/poller_settings/${platform}/status/`, { credentials: 'same-origin' })
      if (r.ok) {
        const j = await r.json()
        setEnabled(j.enabled)
        setItemsPerTick(j.items_per_tick)
        setIntervalValue(j.interval_value)
        setIntervalUnit(j.interval_unit)
        setBackfillPages(j.backfill_pages_per_tick)
      }
    } catch (_) {}
  }, [platform])

  useEffect(() => { load() }, [load])

  async function saveSettings(next) {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const r = await fetch(`/api/poller_settings/${platform}/set/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({
          enabled: next.enabled, items_per_tick: next.itemsPerTick,
          interval_value: next.intervalValue, interval_unit: next.intervalUnit,
          backfill_pages_per_tick: next.backfillPages,
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `保存に失敗しました (${r.status})`)
      setEnabled(j.enabled)
      setItemsPerTick(j.items_per_tick)
      setIntervalValue(j.interval_value)
      setIntervalUnit(j.interval_unit)
      setBackfillPages(j.backfill_pages_per_tick)
      setNotice('保存しました。')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ marginBottom: 20, padding: '12px', border: '1px solid #334155', borderRadius: 6 }}>
      <div style={{ marginBottom: 10 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <input
            type="checkbox" checked={enabled}
            onChange={e => {
              const next = e.target.checked
              setEnabled(next)
              saveSettings({ enabled: next, itemsPerTick, intervalValue, intervalUnit, backfillPages })
            }}
          />
          <strong>{label}</strong>
        </label>
        <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
          オフの間は裏で一切取得を行いません。オンにすると下記の頻度・件数で継続的に取得します。
        </div>
      </div>

      {error && <div style={{ color: '#f87171', marginBottom: 8, fontSize: 13 }}>{error}</div>}
      {notice && <div style={{ color: '#4ade80', marginBottom: 8, fontSize: 13 }}>{notice}</div>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={{ fontSize: 13 }}>件数</span>
        <input
          type="number" min="1" value={itemsPerTick}
          onChange={e => setItemsPerTick(parseInt(e.target.value, 10) || 1)}
          style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
            borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
        />
        <span style={{ fontSize: 13 }}>件を</span>
        <input
          type="number" min="1" value={intervalValue}
          onChange={e => setIntervalValue(parseInt(e.target.value, 10) || 1)}
          style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
            borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
        />
        <select
          value={intervalUnit} onChange={e => setIntervalUnit(e.target.value)}
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
          type="number" min="1" value={backfillPages}
          onChange={e => setBackfillPages(parseInt(e.target.value, 10) || 1)}
          style={{ width: 60, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155',
            borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
        />
        <span style={{ fontSize: 13 }}>ページずつ(未処理の古い投稿に追いつくまでの速さ。値を上げるほど早く最古まで到達しますが、1回あたりのリクエスト数が増えます)</span>
      </div>

      <button
        className="btn" style={{ fontSize: 13 }}
        disabled={saving}
        onClick={() => saveSettings({ enabled, itemsPerTick, intervalValue, intervalUnit, backfillPages })}
      >
        {saving ? '保存中…' : '頻度・件数を保存'}
      </button>
    </div>
  )
}
