import React, { useState, useEffect, useCallback } from 'react'
import RegionAnnotator from './RegionAnnotator'
import { notify } from '../lib/crossWindowSync'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

export default function BrokenRegionsModal({ onClose }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [items, setItems] = useState([])
  const [busyId, setBusyId] = useState(null)
  const [fixingAll, setFixingAll] = useState(false)
  const [editingItem, setEditingItem] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/items/broken_regions/', { credentials: 'same-origin', cache: 'no-store' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `取得に失敗しました (${r.status})`)
      setItems(Array.isArray(j.items) ? j.items : [])
    } catch (e) {
      setError(e.message || '読み込みに失敗しました')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function fixOne(itemId) {
    setBusyId(itemId)
    try {
      const r = await fetch(`/api/items/${itemId}/fix_broken_regions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `修復に失敗しました (${r.status})`)
      if (j.item) notify('item-updated', { id: itemId, item: j.item })
      setItems(prev => prev.filter(it => it.id !== itemId))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  async function fixAll() {
    if (!window.confirm(`${items.length}件のアイテムの異常な矩形を一括修復しますか？\n（画像範囲外・無効矩形は削除され、一部はみ出しは画像サイズに収まります）`)) {
      return
    }
    setFixingAll(true)
    setError('')
    try {
      const r = await fetch('/api/items/fix_all_broken_regions/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `一括修復に失敗しました (${r.status})`)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setFixingAll(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()} style={{ maxWidth: 960, width: '94vw', maxHeight: '90vh' }}>
        <div className="cgm-panel-header">
          <div>
            <strong>領域矩形エラー修正</strong>
            <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--muted)' }}>
              {loading ? '検査中…' : `${items.length}件のエラー`}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button className="btn" style={{ fontSize: 12 }} onClick={load} disabled={loading || fixingAll}>再検査</button>
            {items.length > 0 && (
              <button
                className="btn"
                style={{ fontSize: 12, background: '#2563eb', color: '#fff' }}
                onClick={fixAll}
                disabled={fixingAll || loading}
              >
                {fixingAll ? '一括修復中…' : `全${items.length}件を自動修復`}
              </button>
            )}
            <button className="cgm-panel-close" onClick={onClose}>✕</button>
          </div>
        </div>

        {error && (
          <div style={{ padding: '8px 16px', background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: 13 }}>
            {error}
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {editingItem ? (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <strong style={{ fontSize: 15 }}>Item #{editingItem.id} の領域手動修正</strong>
                <button className="btn" onClick={() => setEditingItem(null)}>一覧に戻る</button>
              </div>
              <RegionAnnotator
                item={editingItem}
                onSaved={async (updatedItem) => {
                  notify('item-updated', { id: editingItem.id, item: updatedItem })
                  setEditingItem(null)
                  await load()
                }}
              />
            </div>
          ) : loading ? (
            <div className="cgm-empty-hint">全アイテムの矩形座標を検査しています…</div>
          ) : items.length === 0 ? (
            <div className="cgm-empty-hint">領域矩形がバグっているアイテムはありません 🎉</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {items.map(it => (
                <div
                  key={it.id}
                  style={{
                    background: 'var(--card)',
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: 12,
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 12,
                    alignItems: 'flex-start',
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 260 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <strong>Item #{it.id}</strong>
                      {it.artist && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{it.artist}</span>}
                      {it.situation && <span className={`situation-chip s-${it.situation.toLowerCase()}`}>{it.situation}</span>}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {(it.issues || []).map((iss, idx) => (
                        <div key={idx} style={{ fontSize: 12, color: '#fca5a5' }}>
                          • 画像#{(iss.image_index ?? 0) + 1}: {iss.reason_label}
                          {Array.isArray(iss.characters) && iss.characters.length > 0 && (
                            <span style={{ color: 'var(--muted)', marginLeft: 6 }}>[{iss.characters.join(', ')}]</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                    <button
                      className="btn"
                      style={{ fontSize: 12 }}
                      onClick={() => setEditingItem(it)}
                      disabled={busyId === it.id || fixingAll}
                    >
                      手動で修正
                    </button>
                    <button
                      className="btn"
                      style={{ fontSize: 12, background: '#2563eb', color: '#fff' }}
                      onClick={() => fixOne(it.id)}
                      disabled={busyId === it.id || fixingAll}
                    >
                      {busyId === it.id ? '修復中…' : '自動修復'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
