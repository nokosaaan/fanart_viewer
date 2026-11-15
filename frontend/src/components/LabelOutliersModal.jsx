import React, { useState, useEffect, useCallback, useMemo } from 'react'
import RegionAnnotator from './RegionAnnotator'
import { notify } from '../lib/crossWindowSync'

const DISMISSED_KEY = 'fv_label_outlier_dismissed_v1'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

function loadDismissed() {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(arr) ? arr : [])
  } catch (_) {
    return new Set()
  }
}

function saveDismissed(setObj) {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify([...setObj]))
  } catch (_) {}
}

export default function LabelOutliersModal({ onClose }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [infoMsg, setInfoMsg] = useState('')
  const [meta, setMeta] = useState(null)
  const [outliers, setOutliers] = useState([])
  const [filterText, setFilterText] = useState('')
  const [minProb, setMinProb] = useState(0.35)
  const [dismissed, setDismissed] = useState(() => loadDismissed())
  const [busyKey, setBusyKey] = useState(null)
  const [editingItem, setEditingItem] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    setInfoMsg('')
    try {
      const r = await fetch(`/api/items/label_outliers/?min_prob=${minProb}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `取得に失敗しました (${r.status})`)
      if (j.detail) setInfoMsg(j.detail)
      setMeta({
        modelName: j.model_name,
        featureSource: j.feature_source,
        totalChecked: j.total_checked || 0,
        outlierCount: j.outlier_count || 0,
      })
      setOutliers(Array.isArray(j.outliers) ? j.outliers : [])
    } catch (e) {
      setError(e.message || '読み込みに失敗しました')
    } finally {
      setLoading(false)
    }
  }, [minProb])

  useEffect(() => { load() }, [load])

  const visibleOutliers = useMemo(() => {
    const q = filterText.trim().toLowerCase()
    return outliers.filter(o => {
      if (dismissed.has(`${o.key}:${o.assigned_char}`)) return false
      if (!q) return true
      return (
        String(o.item_id).includes(q) ||
        (o.assigned_char || '').toLowerCase().includes(q) ||
        (o.predicted_char || '').toLowerCase().includes(q) ||
        (o.nearest_cluster_char || '').toLowerCase().includes(q) ||
        (o.artist || '').toLowerCase().includes(q)
      )
    })
  }, [outliers, dismissed, filterText])

  async function applyFix(o, newChar) {
    if (!newChar) return
    setBusyKey(o.key)
    setError('')
    try {
      const r = await fetch(`/api/items/${o.item_id}/apply_label_fix/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify({
          old_char: o.assigned_char,
          new_char: newChar,
          is_region: Boolean(o.is_region),
        }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `更新に失敗しました (${r.status})`)
      if (j.item) notify('item-updated', { id: o.item_id, item: j.item })
      setOutliers(prev => prev.filter(x => !(x.key === o.key && x.assigned_char === o.assigned_char)))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyKey(null)
    }
  }

  function dismissOne(o) {
    const k = `${o.key}:${o.assigned_char}`
    setDismissed(prev => {
      const next = new Set(prev)
      next.add(k)
      saveDismissed(next)
      return next
    })
  }

  async function openManualRegionEdit(itemId) {
    try {
      const r = await fetch(`/api/items/${itemId}/`, { credentials: 'same-origin', cache: 'no-store' })
      if (!r.ok) throw new Error(`Item #${itemId} の取得に失敗しました`)
      const it = await r.json()
      setEditingItem(it)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="cgm-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()} style={{ maxWidth: 1040, width: '95vw', maxHeight: '90vh' }}>
        <div className="cgm-panel-header">
          <div>
            <strong>誤ラベル候補チェック（学習クラスタ外れ値）</strong>
            {meta && meta.modelName && (
              <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--muted)' }}>
                {meta.totalChecked}件中 {visibleOutliers.length}件表示 ({meta.modelName})
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button className="btn" style={{ fontSize: 12 }} onClick={load} disabled={loading}>再分析</button>
            <button className="cgm-panel-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="cgm-panel-search" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="キャラ名・Item ID・作者で絞り込み…"
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            style={{ flex: '1 1 220px', minWidth: 180 }}
          />
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, color: 'var(--muted)' }}>
            最低予測確信度:
            <select value={minProb} onChange={e => setMinProb(Number(e.target.value))} style={{ fontSize: 12 }}>
              <option value={0.25}>25%以上（広め）</option>
              <option value={0.35}>35%以上（標準）</option>
              <option value={0.5}>50%以上（高確信のみ）</option>
              <option value={0.7}>70%以上（厳格）</option>
            </select>
          </label>
          {dismissed.size > 0 && (
            <button
              className="btn"
              style={{ fontSize: 11 }}
              onClick={() => { setDismissed(new Set()); saveDismissed(new Set()) }}
            >
              スキップ解除 ({dismissed.size}件)
            </button>
          )}
        </div>

        {error && (
          <div style={{ padding: '8px 16px', background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: 13 }}>
            {error}
          </div>
        )}
        {infoMsg && (
          <div style={{ padding: '10px 16px', background: 'rgba(59,130,246,0.1)', color: '#93c5fd', fontSize: 13 }}>
            {infoMsg}
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {editingItem ? (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <strong style={{ fontSize: 15 }}>Item #{editingItem.id} の領域・キャラ確認</strong>
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
            <div className="cgm-empty-hint">学習済みモデルとクラスタ重心を比較しています…</div>
          ) : visibleOutliers.length === 0 ? (
            <div className="cgm-empty-hint">外れ値と判定された誤ラベル候補はありません 🎉</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {visibleOutliers.map(o => {
                const previewUrl = o.first_preview_id != null
                  ? `/api/items/${o.item_id}/preview/?id=${o.first_preview_id}`
                  : `/api/items/${o.item_id}/preview/?index=0`
                const viewerHref = `/?image_viewer=1&src=${encodeURIComponent(previewUrl)}`
                const isBusy = busyKey === o.key
                return (
                  <div
                    key={`${o.key}:${o.assigned_char}`}
                    style={{
                      background: 'var(--card)',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      padding: 12,
                      display: 'flex',
                      gap: 14,
                      alignItems: 'flex-start',
                      flexWrap: 'wrap',
                    }}
                  >
                    <a
                      href={viewerHref}
                      target="_blank"
                      rel="noreferrer"
                      title="新しいタブで画像を開く（カラー検出付き）"
                      style={{ flexShrink: 0, display: 'block', borderRadius: 6, overflow: 'hidden', lineHeight: 0 }}
                    >
                      <img
                        src={previewUrl}
                        alt={`item-${o.item_id}`}
                        loading="lazy"
                        style={{ width: 110, height: 110, objectFit: 'cover', display: 'block', background: '#0f172a' }}
                      />
                    </a>

                    <div style={{ flex: 1, minWidth: 260 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                        <strong>Item #{o.item_id}</strong>
                        <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#1e293b', color: '#cbd5e1' }}>
                          {o.is_region ? '領域ラベル' : 'SOLO画像'}
                        </span>
                        {o.situation && <span className={`situation-chip s-${o.situation.toLowerCase()}`}>{o.situation}</span>}
                        {o.artist && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{o.artist}</span>}
                        {o.link && (
                          <a href={o.link} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: '#60a5fa' }}>
                            元リンク
                          </a>
                        )}
                      </div>

                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', fontSize: 12, marginBottom: 8 }}>
                        <span style={{ background: 'rgba(239,68,68,0.16)', color: '#fca5a5', padding: '3px 8px', borderRadius: 6 }}>
                          現在: <strong>{o.assigned_char}</strong> ({Math.round(o.assigned_prob * 100)}% / 類似度 {Math.round(o.own_cluster_sim * 100)}%)
                        </span>
                        <span>→</span>
                        <span style={{ background: 'rgba(59,130,246,0.18)', color: '#93c5fd', padding: '3px 8px', borderRadius: 6 }}>
                          モデル予測: <strong>{o.predicted_char}</strong> ({Math.round(o.predicted_prob * 100)}%)
                        </span>
                        {o.nearest_cluster_char && o.nearest_cluster_char !== o.assigned_char && (
                          <span style={{ background: 'rgba(16,185,129,0.16)', color: '#6ee7b7', padding: '3px 8px', borderRadius: 6 }}>
                            近いクラスタ: <strong>{o.nearest_cluster_char}</strong> (類似度 {Math.round(o.nearest_cluster_sim * 100)}%)
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {o.predicted_char !== o.assigned_char && (
                          <button
                            className="btn"
                            style={{ fontSize: 12, background: '#2563eb', color: '#fff' }}
                            onClick={() => applyFix(o, o.predicted_char)}
                            disabled={isBusy}
                          >
                            「{o.predicted_char}」に変更
                          </button>
                        )}
                        {o.nearest_cluster_char &&
                          o.nearest_cluster_char !== o.assigned_char &&
                          o.nearest_cluster_char !== o.predicted_char && (
                            <button
                              className="btn"
                              style={{ fontSize: 12, background: '#059669', color: '#fff' }}
                              onClick={() => applyFix(o, o.nearest_cluster_char)}
                              disabled={isBusy}
                            >
                              「{o.nearest_cluster_char}」に変更
                            </button>
                        )}
                        <button
                          className="btn"
                          style={{ fontSize: 12 }}
                          onClick={() => openManualRegionEdit(o.item_id)}
                          disabled={isBusy}
                        >
                          領域を手動確認
                        </button>
                        <button
                          className="btn"
                          style={{ fontSize: 12, color: 'var(--muted)' }}
                          onClick={() => dismissOne(o)}
                          disabled={isBusy}
                        >
                          問題なし（スキップ）
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
