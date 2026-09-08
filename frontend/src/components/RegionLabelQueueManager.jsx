import React, { useState, useEffect, useCallback } from 'react'
import RegionAnnotator from './RegionAnnotator'
import CharacterPicker from './CharacterPicker'
import { notify } from '../lib/crossWindowSync'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Mirrors ItemViewSet.region_label_queue's server-side logic, for the
// currentPageItems (client-side) mode — "not yet fully labeled": either
// never touched, or missing a box for some already-confirmed
// item.characters name (region labeling is normally finished AFTER the
// edit queue in this app's own workflow, so a still-unboxed confirmed name
// almost always just means labeling hasn't reached that person yet, not a
// conflict — see region_mismatch_queue's own reasoning for the direction
// that IS treated as a conflict). Excludes SOLO (only one person — nothing
// to disambiguate) and R18 (kept out of this queue per explicit request).
function isEligible(it) {
  if (it.situation === 'SOLO' || it.situation === 'R18') return false
  if (!Array.isArray(it.character_regions) || it.character_regions.length === 0) return true
  return characterDiff(it).itemOnly.length > 0
}

// Mirrors ItemViewSet.region_mismatch_queue's server-side logic, for the
// currentPageItems (client-side) mode.
function characterDiff(it) {
  const regionChars = new Set()
  for (const r of (it.character_regions || [])) for (const c of (r.characters || [])) regionChars.add(c)
  const itemChars = new Set(it.characters || [])
  return {
    regionOnly: [...regionChars].filter(c => !itemChars.has(c)).sort(),
    itemOnly: [...itemChars].filter(c => !regionChars.has(c)).sort(),
  }
}

// A region naming someone item.characters doesn't even recognize is a
// genuine conflict. The reverse (a confirmed name with no box yet) is
// deliberately NOT treated as a mismatch here — see region_label_queue's
// docstring and isEligible above for why that's normally just incomplete
// labeling, not a conflict, and characterDiff's own comment for the
// data-loss trap that mixing the two used to create for "統一する".
function isMismatched(it) {
  return characterDiff(it).regionOnly.length > 0
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
  const [allChars, setAllChars] = useState([])
  const [charList, setCharList] = useState([])
  const [saving, setSaving] = useState(false)
  // Whether RegionAnnotator has unsaved box/label edits for the currently
  // selected item — "スキップ" and friends used to discard this silently
  // (RegionAnnotator unmounts, its own `boxes` state is just gone), which
  // is exactly how a real labeling session was lost while item.characters
  // (confirmed separately, via the edit queue) stayed intact — reported as
  // "labeled the boxes, but character_regions never actually saved".
  const [regionDirty, setRegionDirty] = useState(false)

  useEffect(() => {
    fetch('/api/items/all_characters/').then(r => r.json()).then(d => { if (Array.isArray(d)) setAllChars(d) }).catch(() => {})
  }, [])

  const endpoint = mode === 'mismatch' ? '/api/items/region_mismatch_queue/' : '/api/items/region_label_queue/'

  const load = useCallback(async () => {
    setSelectedId(null)

    if (currentPageItems) {
      const list = currentPageItems.filter(mode === 'mismatch' ? isMismatched : isEligible)
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

  // Reset the manual-fix editor to the currently-selected item's own
  // characters whenever selection changes — otherwise a leftover edit from
  // the previous item would silently carry over.
  useEffect(() => {
    setCharList(selected ? (selected.characters || []) : [])
    setRegionDirty(false)
  }, [selected && selected.id])

  // Guard for anything that would throw away RegionAnnotator's in-progress
  // (unsaved) box/label work: skipping to another item, switching mode
  // tabs, or closing the window. Returns true (proceed) when there's
  // nothing to lose, or the user confirmed discarding it.
  function confirmDiscardIfDirty() {
    if (!regionDirty) return true
    return window.confirm('保存されていない領域ラベルの変更があります。破棄しますか？')
  }

  function selectItem(id) {
    if (id === selectedId) return
    if (!confirmDiscardIfDirty()) return
    setSelectedId(id)
  }

  function changeMode(m) {
    if (m === mode) return
    if (!confirmDiscardIfDirty()) return
    setMode(m)
  }

  function handleClose() {
    if (!confirmDiscardIfDirty()) return
    onClose()
  }

  function skipCurrent() {
    if (!confirmDiscardIfDirty()) return
    selectNext(selected.id)
  }

  async function saveManualFix() {
    if (!selected || saving) return
    setSaving(true)
    try {
      const r = await fetch(`/api/items/${selected.id}/update_fields/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ characters: charList }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || r.status)
      notify('item-updated', { id: selected.id, item: data.item })
      selectNext(selected.id)
    } catch (e) {
      alert('保存に失敗: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  async function syncToRegions() {
    if (!selected || saving) return
    setSaving(true)
    try {
      const r = await fetch(`/api/items/${selected.id}/sync_characters_to_regions/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || r.status)
      notify('item-updated', { id: selected.id, item: data.item })
      selectNext(selected.id)
    } catch (e) {
      alert('保存に失敗: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  const content = (
    <>
      <div className="cgm-panel-header">
        <strong>領域ラベル付けキュー — 複数キャラ画像 ({count}件)</strong>
        <button className="cgm-panel-close" onClick={handleClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
      </div>

      <div style={{ display: 'flex', gap: 6, padding: '8px 12px 0' }}>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'unlabeled' ? 700 : 400, background: mode === 'unlabeled' ? '#eff6ff' : undefined }}
          onClick={() => changeMode('unlabeled')}
        >未ラベル</button>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'mismatch' ? 700 : 400, background: mode === 'mismatch' ? '#fef2f2' : undefined }}
          onClick={() => changeMode('mismatch')}
        >不整合あり</button>
      </div>

      <div className="cgm-panel-search" style={{ fontSize: 12, color: '#6b7280' }}>
        {mode === 'mismatch'
          ? '領域ラベルに、編集キューのキャラ一覧には無い名前が使われているアイテムです(=編集で削除された後に取り残された可能性がある本当の食い違い)。プレビューを見ながら、手動で修正するか領域ラベル側の名前を復元するか判断してください。'
          : 'situationがSOLO・R18以外で、まだ全員分の領域ラベルが付いていないアイテムが対象です(未着手・一部だけ済み、どちらも含む)。検出された矩形をクリックしてキャラ名を割り当て、保存すると次のアイテムに進みます。'}
      </div>

      <div style={{ display: 'flex', minHeight: 0, flex: '1 1 auto' }}>
        <div style={{ width: 220, borderRight: '1px solid #f3f4f6', overflowY: 'auto', flexShrink: 0 }}>
          {loading && <div className="cgm-empty-hint" style={{ padding: 12 }}>読み込み中…</div>}
          {!loading && items.length === 0 && (
            <div className="cgm-empty-hint" style={{ padding: 12 }}>該当するアイテムはありません 🎉</div>
          )}
          {items.map(it => {
            const diff = mode === 'mismatch'
              ? ('region_only_characters' in it
                  ? { regionOnly: it.region_only_characters || [], itemOnly: it.item_only_characters || [] }
                  : characterDiff(it))
              : null
            return (
              <div
                key={it.id}
                onClick={() => selectItem(it.id)}
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
                {diff && diff.regionOnly.length > 0 && (
                  <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>
                    領域のみ: {diff.regionOnly.join(', ')}
                  </div>
                )}
                {diff && diff.itemOnly.length > 0 && (
                  <div style={{ fontSize: 11, color: '#d97706', marginTop: 2 }}>
                    編集キューのみ: {diff.itemOnly.join(', ')}
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
          ) : mode === 'mismatch' ? (
            (() => {
              const diff = 'region_only_characters' in selected
                ? { regionOnly: selected.region_only_characters || [], itemOnly: selected.item_only_characters || [] }
                : characterDiff(selected)
              return (
                <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                    <button className="btn" style={{ padding: '4px 10px' }} onClick={skipCurrent}>スキップ（後で対応）</button>
                  </div>

                  <img
                    src={`/api/items/${selected.id}/preview/`}
                    alt=""
                    style={{ maxWidth: '100%', maxHeight: 420, display: 'block', margin: '0 auto 16px', borderRadius: 6 }}
                  />

                  <div style={{ display: 'flex', gap: 24, marginBottom: 16, fontSize: 13 }}>
                    <div>
                      <div style={{ color: '#f87171', fontWeight: 600, marginBottom: 4 }}>領域ラベルのみ</div>
                      <div style={{ color: '#cbd5e1' }}>{diff.regionOnly.length > 0 ? diff.regionOnly.join(', ') : 'なし'}</div>
                    </div>
                    <div>
                      <div style={{ color: '#fbbf24', fontWeight: 600, marginBottom: 4 }}>編集キューのみ</div>
                      <div style={{ color: '#cbd5e1' }}>{diff.itemOnly.length > 0 ? diff.itemOnly.join(', ') : 'なし'}</div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                    <button className="btn" disabled={saving} onClick={syncToRegions}>
                      領域ラベルの名前を編集キューに復元(追加のみ、既存の名前は消しません)
                    </button>
                  </div>

                  <div style={{ background: '#0f172a', borderRadius: 6, padding: 12 }}>
                    <div style={{ color: '#f8fafc', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>手動で修正</div>
                    <CharacterPicker charList={charList} setCharList={setCharList} allChars={allChars} titles={selected.titles || []} />
                    <button className="btn" style={{ marginTop: 8 }} disabled={saving} onClick={saveManualFix}>この内容で保存</button>
                  </div>
                </div>
              )
            })()
          ) : (
            <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                <button className="btn" style={{ padding: '4px 10px' }} onClick={skipCurrent}>スキップ（後で対応）</button>
              </div>
              <RegionAnnotator
                key={selected.id}
                item={selected}
                onDirtyChange={setRegionDirty}
                onSaved={(newItem) => {
                  // character_regions_view's save is ADD-only, so it can
                  // never by itself create a region_mismatch_queue conflict
                  // (that only happens later, if item.characters is
                  // subsequently edited to remove a name a region still
                  // uses) — nothing to immediately re-check here. Any
                  // still-unboxed confirmed name just means this item
                  // naturally stays in (or re-enters) "未ラベル" until
                  // labeling actually covers everyone — see
                  // region_label_queue's own docstring.
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
    <div className="cgm-panel-backdrop" onClick={handleClose}>
      <div className="cgm-panel" style={{ width: 1000 }} onClick={e => e.stopPropagation()}>{content}</div>
    </div>
  )
}
