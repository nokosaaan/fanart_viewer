import React, { useState, useEffect, useCallback } from 'react'
import RegionAnnotator from './RegionAnnotator'
import { notify } from '../lib/crossWindowSync'

// Excludes SOLO (only one person — nothing to disambiguate) and R18 (kept
// out of this queue per explicit request) — mirrors the server-side filter
// in ItemViewSet.region_label_queue exactly, for the currentPageItems
// (client-side) mode.
function isEligible(it) {
  if (it.situation === 'SOLO' || it.situation === 'R18') return false
  if (Array.isArray(it.character_regions) && it.character_regions.length > 0) return false
  return true
}

// Mirrors ItemViewSet.region_mismatch_queue's server-side logic, for the
// currentPageItems (client-side) mode — a region-labeled character that's
// no longer in item.characters (most likely: someone corrected
// item.characters afterward via the edit queue and forgot to also fix the
// region that still names it — see character_regions_view's own docstring:
// saving a region only ever ADDS a missing name, never removes one).
function regionMismatchCharacters(it) {
  if (!Array.isArray(it.character_regions) || it.character_regions.length === 0) return []
  const regionChars = new Set()
  for (const r of it.character_regions) for (const c of (r.characters || [])) regionChars.add(c)
  const itemChars = new Set(it.characters || [])
  return [...regionChars].filter(c => !itemChars.has(c)).sort()
}

// Mailbox-style bulk review for manually labeling which detected person is
// which character in a multi-character (CP/MULTIPLE/etc.) image — the
// ground-truth counterpart to train_character_classifier.py's automatic
// bootstrap pseudo-labeling (see RegionAnnotator.jsx). Same
// overlay-vs-standalone-window / currentPageItems-vs-server-query split as
// EditQueueManager.jsx — see that component's own comments for the full
// reasoning (id-cursor pagination to avoid skipping items as the queue
// shrinks, standalone falls back to querying the server since it has no
// page to scope to).
export default function RegionLabelQueueManager({ onClose, standalone = false, currentPageItems = null }) {
  // 'unlabeled' = 領域ラベル未設定のアイテム, 'mismatch' = 領域ラベルと
  // item.characters(編集キューでの結果)に食い違いがあるアイテム — 領域指定の
  // 方が信頼度が高いという前提で、食い違いを見つけて手動修正できるようにする。
  const [mode, setMode] = useState('unlabeled')
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [nextBeforeId, setNextBeforeId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [selectedId, setSelectedId] = useState(null)

  const endpoint = mode === 'mismatch' ? '/api/items/region_mismatch_queue/' : '/api/items/region_label_queue/'

  const load = useCallback(async () => {
    setSelectedId(null)

    if (currentPageItems) {
      const list = currentPageItems.filter(mode === 'mismatch' ? (it => regionMismatchCharacters(it).length > 0) : isEligible)
      setItems(list)
      setCount(list.length)
      setHasMore(false)
      setNextBeforeId(null)
      return
    }

    setLoading(true)
    try {
      const r = await fetch(endpoint)
      const data = await r.json().catch(() => ({}))
      const list = data.results || []
      setItems(list)
      setCount(data.count ?? list.length)
      setHasMore(!!data.has_more)
      setNextBeforeId(data.next_before_id ?? null)
    } catch (e) {
      console.error('Failed to load region label queue', e)
      setItems([]); setCount(0); setHasMore(false); setNextBeforeId(null)
    } finally {
      setLoading(false)
    }
  }, [currentPageItems, mode, endpoint])

  useEffect(() => { load() }, [load])

  async function loadMore() {
    if (!hasMore || nextBeforeId == null || loadingMore) return
    setLoadingMore(true)
    try {
      const r = await fetch(`${endpoint}?before_id=${nextBeforeId}`)
      const data = await r.json().catch(() => ({}))
      const list = data.results || []
      setItems(prev => [...prev, ...list])
      setHasMore(!!data.has_more)
      setNextBeforeId(data.next_before_id ?? null)
    } catch (e) {
      console.error('Failed to load more region label queue items', e)
    } finally {
      setLoadingMore(false)
    }
  }

  function selectNext(fromId) {
    setItems(prev => {
      const idx = prev.findIndex(it => it.id === fromId)
      const rest = prev.filter(it => it.id !== fromId)
      const nextItem = rest[Math.min(idx, rest.length - 1)]
      setSelectedId(nextItem ? nextItem.id : null)
      return rest
    })
    setCount(c => Math.max(0, c - 1))
  }

  const selected = items.find(it => it.id === selectedId) || null

  const content = (
    <>
      <div className="cgm-panel-header">
        <strong>領域ラベル付けキュー — 複数キャラ画像 ({count}件)</strong>
        <button className="cgm-panel-close" onClick={onClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
      </div>

      <div style={{ display: 'flex', gap: 6, padding: '8px 12px 0' }}>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'unlabeled' ? 700 : 400, background: mode === 'unlabeled' ? '#eff6ff' : undefined }}
          onClick={() => setMode('unlabeled')}
        >未ラベル</button>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'mismatch' ? 700 : 400, background: mode === 'mismatch' ? '#fef2f2' : undefined }}
          onClick={() => setMode('mismatch')}
        >不整合あり</button>
      </div>

      <div className="cgm-panel-search" style={{ fontSize: 12, color: '#6b7280' }}>
        {mode === 'mismatch'
          ? '領域ラベルに含まれるキャラが編集キューでのキャラ一覧から外れている(=食い違いがある)アイテムです。領域指定の方が信頼度が高いので、どちらが正しいか確認して修正してください。'
          : 'situationがSOLO・R18以外で、まだ領域ラベル未設定のアイテムが対象です。検出された矩形をクリックしてキャラ名を割り当て、保存すると次のアイテムに進みます。'}
      </div>

      <div style={{ display: 'flex', minHeight: 0, flex: '1 1 auto' }}>
        <div style={{ width: 220, borderRight: '1px solid #f3f4f6', overflowY: 'auto', flexShrink: 0 }}>
          {loading && <div className="cgm-empty-hint" style={{ padding: 12 }}>読み込み中…</div>}
          {!loading && items.length === 0 && (
            <div className="cgm-empty-hint" style={{ padding: 12 }}>該当するアイテムはありません 🎉</div>
          )}
          {items.map(it => {
            const mismatchChars = mode === 'mismatch' ? (it.region_mismatch_characters || regionMismatchCharacters(it)) : []
            return (
              <div
                key={it.id}
                onClick={() => setSelectedId(it.id)}
                style={{
                  padding: '10px 12px', cursor: 'pointer',
                  background: it.id === selectedId ? '#eff6ff' : 'transparent',
                  borderBottom: '1px solid #f3f4f6',
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600 }}>#{it.id}</div>
                <div style={{ fontSize: 12, color: '#6b7280' }}>
                  situation: {it.situation || '—'} · キャラ{(it.characters || []).length}件
                </div>
                {mode === 'mismatch' && mismatchChars.length > 0 && (
                  <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>
                    ⚠ {mismatchChars.join(', ')}
                  </div>
                )}
              </div>
            )
          })}
          {hasMore && (
            <button className="btn" style={{ width: '100%', margin: '8px 0', fontSize: 12 }} onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? '読み込み中…' : 'もっと読み込む'}
            </button>
          )}
        </div>

        <div style={{ flex: 1, padding: 16, overflowY: 'auto' }}>
          {!selected ? (
            <div className="cgm-empty-hint">左のリストから項目を選んでください</div>
          ) : (
            <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                <button className="btn" style={{ padding: '4px 10px' }} onClick={() => selectNext(selected.id)}>スキップ（後で対応）</button>
              </div>
              <RegionAnnotator
                key={selected.id}
                item={selected}
                onSaved={(newItem) => {
                  notify('item-updated', { id: selected.id, item: newItem })
                  selectNext(selected.id)
                }}
              />
            </div>
          )}
        </div>
      </div>
    </>
  )

  if (standalone) {
    return <div className="cgm-panel" style={{ width: '100%', height: '100vh', maxHeight: '100vh', borderRadius: 0 }}>{content}</div>
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 1000 }} onClick={e => e.stopPropagation()}>{content}</div>
    </div>
  )
}
