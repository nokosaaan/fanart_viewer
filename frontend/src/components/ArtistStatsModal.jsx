import React, { useState, useEffect, useMemo, useCallback } from 'react'

const TABS = [
  { id: 'artists', label: '作者', unit: '名', placeholder: '作者名で絞り込み…', unassignedLabel: '作者未設定' },
  { id: 'characters', label: 'キャラクター', unit: '人', placeholder: 'キャラ名で絞り込み…', unassignedLabel: 'キャラ未設定' },
  { id: 'situations', label: 'シチュエーション', unit: '種', placeholder: 'シチュエーションで絞り込み…', unassignedLabel: 'シチュエーション未設定' },
  { id: 'titles', label: 'タイトル', unit: '件', placeholder: 'タイトルで絞り込み…', unassignedLabel: 'タイトル未設定' },
]

export default function ArtistStatsModal({ onClose, onSelectArtist, onSelectFilter = null }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('artists')
  const [filterText, setFilterText] = useState('')
  const [minItems, setMinItems] = useState('')
  const [maxItems, setMaxItems] = useState('')
  const [minImages, setMinImages] = useState('')
  const [maxImages, setMaxImages] = useState('')
  const [sortBy, setSortBy] = useState('items') // 'items' | 'images' | 'name'
  const [sortOrder, setSortOrder] = useState('desc') // 'desc' | 'asc'

  const loadStats = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await fetch('/api/items/stats/', { cache: 'no-store' })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(j.detail || `統計の取得に失敗しました (${resp.status})`)
      setData(j)
    } catch (e) {
      setError(e.message || '統計の取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStats()
  }, [loadStats])

  const tabMeta = TABS.find(t => t.id === activeTab) || TABS[0]
  const currentBucket = data && data[activeTab] ? data[activeTab] : null

  const displayedRows = useMemo(() => {
    const list = (currentBucket && Array.isArray(currentBucket.rows)) ? currentBucket.rows : []
    const q = filterText.trim().toLowerCase()
    const minIt = minItems.trim() !== '' ? Number(minItems) : null
    const maxIt = maxItems.trim() !== '' ? Number(maxItems) : null
    const minIm = minImages.trim() !== '' ? Number(minImages) : null
    const maxIm = maxImages.trim() !== '' ? Number(maxImages) : null
    const filtered = list.filter(row => {
      if (q && !row.name.toLowerCase().includes(q)) return false
      if (minIt !== null && !Number.isNaN(minIt) && row.item_count < minIt) return false
      if (maxIt !== null && !Number.isNaN(maxIt) && row.item_count > maxIt) return false
      if (minIm !== null && !Number.isNaN(minIm) && row.image_count < minIm) return false
      if (maxIm !== null && !Number.isNaN(maxIm) && row.image_count > maxIm) return false
      return true
    })
    const sorted = [...filtered]
    const dir = sortOrder === 'asc' ? 1 : -1
    if (sortBy === 'images') {
      sorted.sort((a, b) => dir * ((a.image_count - b.image_count) || (a.item_count - b.item_count)) || a.name.localeCompare(b.name))
    } else if (sortBy === 'name') {
      sorted.sort((a, b) => dir * a.name.localeCompare(b.name))
    } else {
      sorted.sort((a, b) => dir * ((a.item_count - b.item_count) || (a.image_count - b.image_count)) || a.name.localeCompare(b.name))
    }
    return sorted
  }, [currentBucket, filterText, minItems, maxItems, minImages, maxImages, sortBy, sortOrder])

  const maxCount = useMemo(() => {
    if (displayedRows.length === 0) return 1
    if (sortBy === 'images') {
      return Math.max(1, ...displayedRows.map(r => r.image_count))
    }
    return Math.max(1, ...displayedRows.map(r => r.item_count))
  }, [displayedRows, sortBy])

  const handleSelect = onSelectFilter || onSelectArtist

  return (
    <div className="cgm-panel-backdrop" onClick={onClose} onWheel={e => e.stopPropagation()}>
      <div
        className="cgm-panel"
        style={{ width: 740, maxWidth: '96vw', height: '84vh', maxHeight: '88vh' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="cgm-panel-header">
          <div>
            <strong>統計</strong>
            {data && currentBucket && (
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
                {tabMeta.label} {currentBucket.total_unique}{tabMeta.unit} · 全{data.total_items}件
                {currentBucket.unassigned_items > 0 ? `（${tabMeta.unassignedLabel} ${currentBucket.unassigned_items}件）` : ''}
                {' '}— 項目をクリックすると検索窓で絞り込みます
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button type="button" className="btn" style={{ fontSize: 12 }} onClick={loadStats} disabled={loading}>
              {loading ? '集計中…' : '再集計'}
            </button>
            <button type="button" className="cgm-panel-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 4, padding: '8px 16px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
          {TABS.map(tab => {
            const active = activeTab === tab.id
            const count = data && data[tab.id] ? data[tab.id].total_unique : null
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => { setActiveTab(tab.id); setFilterText('') }}
                style={{
                  padding: '6px 12px',
                  fontSize: 13,
                  fontWeight: active ? 700 : 500,
                  background: active ? 'var(--active-bg)' : 'transparent',
                  color: active ? 'var(--active-text)' : 'var(--muted)',
                  border: 'none',
                  borderBottom: active ? '2px solid #3b82f6' : '2px solid transparent',
                  borderRadius: '6px 6px 0 0',
                  cursor: 'pointer',
                }}
              >
                {tab.label}{count != null ? ` (${count})` : ''}
              </button>
            )
          })}
        </div>

        <div className="cgm-panel-search" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            className="cgm-search-input"
            style={{ flex: '1 1 200px' }}
            placeholder={tabMeta.placeholder}
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {[
              { id: 'items', label: 'アイテム数順' },
              { id: 'images', label: '画像数順' },
              { id: 'name', label: '名前順' },
            ].map(opt => (
              <button
                key={opt.id}
                type="button"
                className="btn"
                onClick={() => {
                  if (sortBy === opt.id) {
                    setSortOrder(prev => (prev === 'desc' ? 'asc' : 'desc'))
                  } else {
                    setSortBy(opt.id)
                    setSortOrder(opt.id === 'name' ? 'asc' : 'desc')
                  }
                }}
                style={{
                  fontSize: 12,
                  padding: '5px 10px',
                  fontWeight: sortBy === opt.id ? 700 : 400,
                  background: sortBy === opt.id ? 'var(--active-bg)' : undefined,
                  color: sortBy === opt.id ? 'var(--active-text)' : undefined,
                }}
              >
                {opt.label}
              </button>
            ))}
            <button
              type="button"
              className="btn"
              onClick={() => setSortOrder(prev => (prev === 'desc' ? 'asc' : 'desc'))}
              style={{ fontSize: 12, padding: '5px 10px' }}
              title="並び順（多い順 / 少ない順）を切り替えます"
            >
              {sortBy === 'name'
                ? (sortOrder === 'asc' ? '↑ 昇順' : '↓ 降順')
                : (sortOrder === 'asc' ? '↑ 少ない順' : '↓ 多い順')}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', width: '100%', fontSize: 12, color: 'var(--muted)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              アイテム数:
              <input
                type="number"
                min={0}
                placeholder="以上"
                value={minItems}
                onChange={e => setMinItems(e.target.value)}
                style={{ width: 64, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
              />
              〜
              <input
                type="number"
                min={0}
                placeholder="以下"
                value={maxItems}
                onChange={e => setMaxItems(e.target.value)}
                style={{ width: 64, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
              />
              件
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              画像数:
              <input
                type="number"
                min={0}
                placeholder="以上"
                value={minImages}
                onChange={e => setMinImages(e.target.value)}
                style={{ width: 64, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
              />
              〜
              <input
                type="number"
                min={0}
                placeholder="以下"
                value={maxImages}
                onChange={e => setMaxImages(e.target.value)}
                style={{ width: 64, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
              />
              枚
            </span>
            {(minItems || maxItems || minImages || maxImages) && (
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => { setMinItems(''); setMaxItems(''); setMinImages(''); setMaxImages('') }}
              >
                数値フィルタ解除
              </button>
            )}
            <span style={{ marginLeft: 'auto', fontSize: 11 }}>
              該当 {displayedRows.length}{tabMeta.unit}
            </span>
          </div>
        </div>

        <div className="cgm-panel-body" style={{ padding: '10px 16px', gap: 6 }}>
          {error && <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>}
          {loading ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>統計を集計しています…</div>
          ) : displayedRows.length === 0 ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>該当する{tabMeta.label}がありません</div>
          ) : (
            displayedRows.map((row, idx) => {
              const value = sortBy === 'images' ? row.image_count : row.item_count
              const pct = Math.max(2, Math.round((value / maxCount) * 100))
              const share = data && data.total_items > 0
                ? ((row.item_count / data.total_items) * 100).toFixed(1)
                : '0.0'
              return (
                <button
                  key={row.name}
                  type="button"
                  onClick={() => {
                    if (handleSelect) handleSelect(row.name)
                    onClose()
                  }}
                  title={`「${row.name}」で検索して絞り込む`}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    background: 'var(--bg)',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    padding: '8px 12px',
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 5,
                    color: 'var(--text)',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg)' }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                      <span style={{ fontSize: 11, color: 'var(--muted)', minWidth: 28 }}>#{idx + 1}</span>
                      <span style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.name}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, fontSize: 12 }}>
                      <span style={{ fontWeight: 700, color: 'var(--active-text)' }}>{row.item_count}件</span>
                      <span style={{ color: 'var(--muted)' }}>({row.image_count}枚)</span>
                      <span style={{ color: 'var(--text-faint)', fontSize: 11, minWidth: 42, textAlign: 'right' }}>{share}%</span>
                    </div>
                  </div>
                  <div style={{ width: '100%', height: 5, background: 'var(--divider)', borderRadius: 999, overflow: 'hidden' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: '#3b82f6', borderRadius: 999 }} />
                  </div>
                </button>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
