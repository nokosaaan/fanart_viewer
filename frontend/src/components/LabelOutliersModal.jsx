import React, { useState, useEffect, useCallback, useMemo } from 'react'
import EditFields from './EditFields'
import { notify } from '../lib/crossWindowSync'

const RESOLVED_KEY = 'fv_label_outlier_resolved_v2'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

function loadResolved(modelVersion = '') {
  try {
    const raw = localStorage.getItem(RESOLVED_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (!parsed || typeof parsed !== 'object') return new Set()
    if (modelVersion && parsed.modelVersion && parsed.modelVersion !== modelVersion) {
      return new Set()
    }
    return new Set(Array.isArray(parsed.keys) ? parsed.keys : [])
  } catch (_) {
    return new Set()
  }
}

function saveResolved(modelVersion, setObj) {
  try {
    localStorage.setItem(RESOLVED_KEY, JSON.stringify({
      modelVersion: modelVersion || '',
      keys: [...setObj],
    }))
  } catch (_) {}
}

export default function LabelOutliersModal({ onClose }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [infoMsg, setInfoMsg] = useState('')
  const [meta, setMeta] = useState(null)
  const [outliers, setOutliers] = useState([])
  const [filterText, setFilterText] = useState('')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [minProb, setMinProb] = useState(0.35)
  const [resolved, setResolved] = useState(() => loadResolved())
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
      const modelVersion = j.model_name ? `${j.model_name}:${j.model_mtime || 0}` : ''
      setMeta({
        modelName: j.model_name,
        modelVersion,
        featureSource: j.feature_source,
        totalChecked: j.total_checked || 0,
        outlierCount: j.outlier_count || 0,
        feedbackSummary: j.feedback_summary || null,
      })
      if (modelVersion) {
        setResolved(loadResolved(modelVersion))
      }
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
      if (resolved.has(`${o.key}:${o.assigned_char}`)) return false
      if (sourceFilter === 'low_acc_holdout' && !(o.from_holdout && o.low_accuracy)) return false
      if (sourceFilter === 'holdout' && !o.from_holdout) return false
      if (sourceFilter === 'cluster' && !o.from_cluster) return false
      if (!q) return true
      return (
        String(o.item_id).includes(q) ||
        (o.assigned_char || '').toLowerCase().includes(q) ||
        (o.predicted_char || '').toLowerCase().includes(q) ||
        (o.nearest_cluster_char || '').toLowerCase().includes(q) ||
        (o.artist || '').toLowerCase().includes(q)
      )
    })
  }, [outliers, resolved, filterText, sourceFilter])

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

  function resolveOne(o) {
    const k = `${o.key}:${o.assigned_char}`
    setResolved(prev => {
      const next = new Set(prev)
      next.add(k)
      saveResolved(meta?.modelVersion || '', next)
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
    <>
      <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()} style={{ maxWidth: 1040, width: '95vw', maxHeight: '90vh' }}>
        <div className="cgm-panel-header">
          <div>
            <strong>誤ラベル候補チェック（外れ値 / 学習フィードバック）</strong>
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
            検出元:
            <select value={sourceFilter} onChange={e => setSourceFilter(e.target.value)} style={{ fontSize: 12 }}>
              <option value="all">すべて</option>
              <option value="low_acc_holdout">検証ミス（低精度キャラ）</option>
              <option value="holdout">検証ミス（Holdout全件）</option>
              <option value="cluster">クラスタ外れ値のみ</option>
            </select>
          </label>
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, color: 'var(--muted)' }}>
            最低予測確信度:
            <select value={minProb} onChange={e => setMinProb(Number(e.target.value))} style={{ fontSize: 12 }}>
              <option value={0.25}>25%以上（広め）</option>
              <option value={0.35}>35%以上（標準）</option>
              <option value={0.5}>50%以上（高確信のみ）</option>
              <option value={0.7}>70%以上（厳格）</option>
            </select>
          </label>
          {resolved.size > 0 && (
            <button
              className="btn"
              style={{ fontSize: 11 }}
              onClick={() => { setResolved(new Set()); saveResolved(meta?.modelVersion || '', new Set()) }}
            >
              解決済みを再表示 ({resolved.size}件)
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

        {meta?.feedbackSummary && meta.feedbackSummary.low_accuracy_chars?.length > 0 && (
          <div style={{ padding: '8px 16px', background: 'rgba(245,158,11,0.08)', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
            <div style={{ color: '#fbbf24', fontWeight: 600, marginBottom: 4 }}>
              Holdout 精度 {Math.round((meta.feedbackSummary.feedback_threshold ?? 0.95) * 100)}% 以下のキャラ（{meta.feedbackSummary.low_accuracy_chars.length}件・クリックで絞り込み）
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: 72, overflowY: 'auto' }}>
              {meta.feedbackSummary.low_accuracy_chars.map(ch => {
                const active = filterText.trim().toLowerCase() === ch.character.toLowerCase()
                return (
                  <button
                    key={ch.character}
                    type="button"
                    onClick={() => setFilterText(active ? '' : ch.character)}
                    style={{
                      fontSize: 11,
                      padding: '2px 7px',
                      borderRadius: 4,
                      border: active ? '1px solid #f59e0b' : '1px solid rgba(245,158,11,0.3)',
                      background: active ? 'rgba(245,158,11,0.25)' : 'rgba(15,23,42,0.6)',
                      color: '#fde68a',
                      cursor: 'pointer',
                    }}
                    title={`Holdout正解率 ${Math.round(ch.accuracy * 100)}% (${ch.hits}/${ch.support})`}
                  >
                    {ch.character} ({Math.round(ch.accuracy * 100)}%, {ch.hits}/{ch.support})
                  </button>
                )
              })}
            </div>
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {loading ? (
            <div className="cgm-empty-hint">学習済みモデルとクラスタ重心を比較しています…</div>
          ) : visibleOutliers.length === 0 ? (
            <div className="cgm-empty-hint">外れ値と判定された誤ラベル候補はありません 🎉</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {visibleOutliers.map(o => {
                const targetPid = o.target_preview_id ?? o.first_preview_id
                const previewUrl = targetPid != null
                  ? `/api/items/${o.item_id}/preview/?id=${targetPid}`
                  : `/api/items/${o.item_id}/preview/?index=${o.image_index ?? 0}`
                const viewerHref = `/?image_viewer=1&src=${encodeURIComponent(previewUrl)}`
                const isBusy = busyKey === o.key
                const pageLabel = o.image_index != null
                  ? (o.preview_count > 1 ? `${o.image_index + 1}枚目 / 全${o.preview_count}枚` : '1枚目')
                  : null
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
                        {pageLabel && (
                          <span style={{
                            fontSize: 11,
                            padding: '1px 6px',
                            borderRadius: 4,
                            background: o.preview_count > 1 ? '#1e3a8a' : '#1e293b',
                            color: o.preview_count > 1 ? '#bfdbfe' : '#94a3b8',
                            fontWeight: o.preview_count > 1 ? 600 : 400,
                          }}>
                            {pageLabel}
                          </span>
                        )}
                        <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#1e293b', color: '#cbd5e1' }}>
                          {o.is_region ? '領域ラベル' : 'SOLO画像'}
                        </span>
                        {o.from_holdout && (
                          <span
                            style={{
                              fontSize: 11, padding: '1px 6px', borderRadius: 4,
                              background: o.low_accuracy ? '#78350f' : '#1e3a8a',
                              color: o.low_accuracy ? '#fde68a' : '#bfdbfe',
                              fontWeight: 600,
                            }}
                            title={o.char_accuracy != null ? `このキャラのHoldout正解率: ${Math.round(o.char_accuracy * 100)}%` : undefined}
                          >
                            {o.low_accuracy && o.char_accuracy != null
                              ? `検証ミス (${Math.round(o.char_accuracy * 100)}%)`
                              : '検証ミス (Holdout)'}
                          </span>
                        )}
                        {o.from_cluster && (
                          <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#1e293b', color: '#94a3b8' }}>
                            クラスタ外れ値
                          </span>
                        )}
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
                          現在: <strong>{o.assigned_char}</strong> ({Math.round(o.assigned_prob * 100)}%{o.from_cluster ? ` / 類似度 ${Math.round(o.own_cluster_sim * 100)}%` : ''})
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
                          手動で編集
                        </button>
                        <button
                          className="btn"
                          style={{ fontSize: 12, background: '#334155', color: '#e2e8f0' }}
                          onClick={() => resolveOne(o)}
                          disabled={isBusy}
                          title="現在の学習モデルでは解決済みとして一覧から除外します（再学習後に再び外れ値になった場合は再表示されます）"
                        >
                          ✓ 解決済み
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
      {editingItem && (
        <EditFields
          item={editingItem}
          onClose={() => setEditingItem(null)}
          onSaved={async (updatedItem) => {
            if (updatedItem) notify('item-updated', { id: editingItem.id, item: updatedItem })
            setEditingItem(null)
            await load()
          }}
        />
      )}
    </>
  )
}
