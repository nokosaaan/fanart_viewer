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
// currentPageItems (client-side) mode — deliberately narrow: "untouched at
// all" only. An item that's been saved at least once (character_regions
// non-empty) graduates out of this queue for good, whatever state it's
// in — any remaining gap is region_mismatch_queue's job from then on (see
// its own docstring for why: a considered decision made there used to get
// silently undone by the item reappearing back here). Excludes SOLO (only
// one person — nothing to disambiguate) and R18 (kept out per explicit
// request).
function isEligible(it) {
  if (it.situation === 'SOLO' || it.situation === 'R18') return false
  return !Array.isArray(it.character_regions) || it.character_regions.length === 0
}

function regionCharsOf(it) {
  const s = new Set()
  for (const r of (it.character_regions || [])) for (const c of (r.characters || [])) s.add(c)
  return s
}

// Mirrors ItemViewSet.region_mismatch_queue's server-side logic, for the
// currentPageItems (client-side) mode (minus the ack-signature exclusion —
// that's server-only bookkeeping this dead code path has no access to;
// currentPageItems is currently never actually passed by any caller).
function characterDiff(it) {
  const regionChars = regionCharsOf(it)
  const itemChars = new Set(it.characters || [])
  return {
    regionOnly: [...regionChars].filter(c => !itemChars.has(c)).sort(),
    itemOnly: [...itemChars].filter(c => !regionChars.has(c)).sort(),
  }
}

function isMismatched(it) {
  if (!Array.isArray(it.character_regions) || it.character_regions.length === 0) return false
  const { regionOnly, itemOnly } = characterDiff(it)
  return regionOnly.length > 0 || itemOnly.length > 0
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
  // 'unlabeled' = 一度も領域ラベルを保存していないアイテム, 'mismatch' =
  // 一度は保存したが、領域ラベルとitem.characters(編集キューでの結果)が
  // 完全一致していないアイテム — 一度保存した後は二度と「未ラベル」には
  // 戻らず、残った食い違いは全て「不整合あり」側で解決する。
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
  // Whether to show RegionAnnotator (instead of the diff/adopt panel) while
  // in mismatch mode, so a remaining gap can be closed by actually
  // finishing the labeling right here instead of only choosing a side.
  const [annotating, setAnnotating] = useState(false)
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

  // Reset per-item UI state whenever selection changes — otherwise a
  // leftover edit / annotator-open state from the previous item would
  // silently carry over.
  useEffect(() => {
    setCharList(selected ? (selected.characters || []) : [])
    setRegionDirty(false)
    setAnnotating(false)
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
    if (!window.confirm('編集キューのキャラ一覧を、領域ラベルの内容で完全に上書きします。よろしいですか？')) return
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

  async function acknowledgeMismatch() {
    if (!selected || saving) return
    setSaving(true)
    try {
      const r = await fetch(`/api/items/${selected.id}/acknowledge_character_mismatch/`, {
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
          ? '一度は領域ラベルを保存したものの、編集キューのキャラ一覧と完全には一致していないアイテムです。両方の一覧を見比べて、どちらを採用するか・そのままでよいか・ここで矩形を追加して解決するかを選んでください。'
          : 'situationがSOLO・R18以外で、まだ一度も領域ラベルを保存していないアイテムが対象です。検出された矩形をクリックしてキャラ名を割り当て、保存すると次のアイテムに進みます(一度保存すれば、以後このタブには戻ってきません)。'}
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
          ) : mode === 'mismatch' && !annotating ? (
            (() => {
              const regionChars = [...regionCharsOf(selected)].sort()
              const itemChars = [...(selected.characters || [])].sort()
              return (
                <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                    <button className="btn" style={{ padding: '4px 10px' }} onClick={skipCurrent}>スキップ（後で対応）</button>
                  </div>

                  <img
                    src={`/api/items/${selected.id}/preview/`}
                    alt=""
                    style={{ maxWidth: '100%', maxHeight: 380, display: 'block', margin: '0 auto 16px', borderRadius: 6 }}
                  />

                  <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
                    <div style={{ flex: 1, background: '#0f172a', borderRadius: 6, padding: 12 }}>
                      <div style={{ color: '#f8fafc', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                        編集キューのキャラ一覧 ({itemChars.length}件)
                      </div>
                      <div style={{ color: '#cbd5e1', fontSize: 13, marginBottom: 10 }}>
                        {itemChars.length > 0 ? itemChars.join(', ') : '(なし)'}
                      </div>
                      <button className="btn" style={{ width: '100%' }} disabled={saving} onClick={acknowledgeMismatch}>
                        こちらを採用(このままでOK・データは変更しない)
                      </button>
                    </div>
                    <div style={{ flex: 1, background: '#0f172a', borderRadius: 6, padding: 12 }}>
                      <div style={{ color: '#f8fafc', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                        領域ラベルのキャラ一覧 ({regionChars.length}件)
                      </div>
                      <div style={{ color: '#cbd5e1', fontSize: 13, marginBottom: 10 }}>
                        {regionChars.length > 0 ? regionChars.join(', ') : '(なし)'}
                      </div>
                      <button className="btn" style={{ width: '100%' }} disabled={saving} onClick={syncToRegions}>
                        こちらを採用(編集キューを上書き)
                      </button>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                    <button className="btn" onClick={() => setAnnotating(true)}>
                      ここで領域指定を続ける(矩形を追加・修正)
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
                <div style={{ display: 'flex', gap: 8 }}>
                  {mode === 'mismatch' && (
                    <button className="btn" style={{ padding: '4px 10px' }} onClick={() => { if (confirmDiscardIfDirty()) setAnnotating(false) }}>
                      一覧表示に戻る
                    </button>
                  )}
                  <button className="btn" style={{ padding: '4px 10px' }} onClick={skipCurrent}>スキップ（後で対応）</button>
                </div>
              </div>
              <RegionAnnotator
                key={selected.id}
                item={selected}
                onDirtyChange={setRegionDirty}
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
    <div className="cgm-panel-backdrop" onClick={handleClose}>
      <div className="cgm-panel" style={{ width: 1000 }} onClick={e => e.stopPropagation()}>{content}</div>
    </div>
  )
}
