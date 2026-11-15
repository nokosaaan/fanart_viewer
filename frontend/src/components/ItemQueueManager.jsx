import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import ItemQueuePanel from './ItemQueuePanel'
import RegionAnnotator from './RegionAnnotator'
import CharacterPicker from './CharacterPicker'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'
import Pagination from './Pagination'

function getCookie(name){
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

const MISSING_FIELDS = [
  { key: 'titles', label: 'タイトル', icon: '📖' },
  { key: 'characters', label: 'キャラクター', icon: '👤' },
  { key: 'tags', label: 'タグ', icon: '🏷' },
  { key: 'situation', label: 'シチュエーション', icon: '🎭' },
  { key: 'artist', label: '作者', icon: '🎨' },
]

function isFieldMissing(it, field){
  if (field === 'situation' || field === 'artist') return !it[field]
  const v = it[field]
  return !Array.isArray(v) || v.length === 0
}

// Mirrors ItemViewSet.region_label_queue's server-side eligibility filter
// (see needs_attention_queue's own OR of this exact condition with the
// missing-fields one below) — situation SOLO has nothing to
// disambiguate, and an item that's already been touched at all
// (character_regions non-empty) belongs to the 'mismatch' tab from then on,
// never back here.
function isEligibleForRegion(it){
  if (it.situation === 'SOLO') return false
  return !Array.isArray(it.character_regions) || it.character_regions.length === 0
}

// Per-item reasons this shows up in the 'attention' tab, for the sidebar
// badges. Server-cursor mode already precomputes `missing_fields`/
// `needs_region` per result (see needs_attention_queue) — prefer those when
// present so the badge always matches exactly what the server filtered on;
// only fall back to deriving them client-side (against the live
// `activeFields` toggle) in allItems-scoped mode, where nothing was fetched
// from the server at all.
function attentionReasons(it, activeFields){
  const missingFields = Array.isArray(it.missing_fields)
    ? it.missing_fields
    : MISSING_FIELDS.filter(f => activeFields.has(f.key) && isFieldMissing(it, f.key)).map(f => f.key)
  const needsRegion = typeof it.needs_region === 'boolean' ? it.needs_region : isEligibleForRegion(it)
  return { missingFields, needsRegion }
}

function isNeedsAttention(it, activeFields){
  const { missingFields, needsRegion } = attentionReasons(it, activeFields)
  return missingFields.length > 0 || needsRegion
}

function regionCharsOf(it) {
  const s = new Set()
  for (const r of (it.character_regions || [])) for (const c of (r.characters || [])) s.add(c)
  return s
}

// Mirrors ItemViewSet.region_mismatch_queue's server-side logic.
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

// Lets the actual source image be checked without leaving this panel at all.
function SourceLink({ item }) {
  if (!item.link) return null
  const platform = getPlatformIcon(item.link)
  return (
    <a className="link-text" href={item.link} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
      {platform ? <img src={platform.icon} alt={platform.label} style={{ width: 14, height: 14, borderRadius: 3 }} /> : null}
      元リンクを開く
    </a>
  )
}

// Unified queue: replaces the former separate 編集キュー(EditQueueManager)
// and 領域ラベル付けキュー(RegionLabelQueueManager) — editing the same
// image's fields and its multi-character regions used to take two entirely
// separate passes through the queue; this puts both on one screen
// (ItemQueuePanel) with one save button. The former "不整合あり" tab
// (region labels vs. item.characters disagreeing after having been touched
// once) stays as its own tab here (`mode === 'mismatch'`) — a fundamentally
// different follow-up task (reconciling two recorded answers) from "nothing
// recorded yet" (`mode === 'attention'`), and unrelated to this merge.
//
// `standalone`/`allItems`/`pageSize`/`initialPage`/`onPopOut`/`hidden`: see
// the former EditQueueManager.jsx/RegionLabelQueueManager.jsx for the full
// design rationale (id-cursor pagination so the queue can't skip items as it
// shrinks, standalone popout via a localStorage handoff, the allItems-scoped
// pager, keeping this mounted-but-hidden across close/reopen so in-progress
// state survives) — both hold identical here.
export default function ItemQueueManager({ onClose, standalone = false, allItems = null, pageSize = 50, initialPage = 0, onPopOut = null, hidden = false }){
  const [activeFields, setActiveFields] = useState(() => new Set(MISSING_FIELDS.map(f => f.key)))
  const [queuePageIndex, setQueuePageIndex] = useState(initialPage || 0)
  const queuePageCount = Array.isArray(allItems) ? Math.max(1, Math.ceil(allItems.length / pageSize)) : 1
  const scopedItems = useMemo(() => (
    Array.isArray(allItems) ? allItems.slice(queuePageIndex * pageSize, (queuePageIndex + 1) * pageSize) : null
  ), [allItems, queuePageIndex, pageSize])

  // 'attention' = フィールド不足または領域未設定のアイテム(統合編集画面)。
  // 'mismatch' = 一度は領域ラベルを保存したものの、編集キューのキャラ一覧と
  // 完全には一致していないアイテム(従来のdiff/adoptパネルをそのまま維持)。
  const [mode, setMode] = useState('attention')
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [nextBeforeId, setNextBeforeId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [allChars, setAllChars] = useState([])
  const [charList, setCharList] = useState([]) // mismatch-tab manual fix only
  const [saving, setSaving] = useState(false) // mismatch-tab actions only

  // Whether ItemQueuePanel (attention tab) has unsaved field/region edits
  // for the currently selected item.
  const [itemPanelDirty, setItemPanelDirty] = useState(false)
  // Whether RegionAnnotator (mismatch tab's own "ここで領域指定を続ける"
  // sub-view) has unsaved box/label edits — same name/contract as the
  // former RegionLabelQueueManager's own `regionDirty`.
  const [regionDirty, setRegionDirty] = useState(false)
  // Whether the mismatch tab is showing RegionAnnotator instead of the
  // diff/adopt panel for the selected item.
  const [annotating, setAnnotating] = useState(false)
  const [settingsMenuOpen, setSettingsMenuOpen] = useState(false)
  const settingsMenuRef = useRef(null)

  useEffect(() => {
    if (!settingsMenuOpen) return
    function onDocMouseDown(e) {
      if (settingsMenuRef.current && !settingsMenuRef.current.contains(e.target)) {
        setSettingsMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [settingsMenuOpen])

  // itemId -> suggestion result (attention tab only). Populated by
  // runSuggestFor so opening an item later applies it instantly instead of
  // waiting on a fresh request (see ItemEditForm's initialSuggestion prop
  // via ItemQueuePanel). Mirrored into a ref so runSuggestFor can check
  // "already have this one" without needing `suggestions` in its own
  // dependency list (see bulkSuggestingRef below for why that matters).
  const [suggestions, setSuggestions] = useState({})
  const suggestionsRef = useRef({})
  useEffect(()=>{ suggestionsRef.current = suggestions }, [suggestions])

  const [bulkSuggesting, setBulkSuggesting] = useState(false)
  const bulkSuggestingRef = useRef(false)
  const [bulkProgress, setBulkProgress] = useState(null) // {done, total, skipped}

  // Kill switch for every bulk loop below (runSuggestFor, bulkSaveTagsOnly)
  // — see the former EditQueueManager.jsx's own comment on cancelledRef/
  // abortRef for the full reasoning; identical here.
  const cancelledRef = useRef(false)
  const abortRef = useRef(null)
  useEffect(() => {
    abortRef.current = new AbortController()
    return () => { cancelledRef.current = true; abortRef.current.abort() }
  }, [])

  const [bulkSaving, setBulkSaving] = useState(false)
  const [bulkSaveProgress, setBulkSaveProgress] = useState(null) // {done, total, failed}

  const [suggestMode] = useState('external')
  const [suggestModel, setSuggestModel] = useState('canary')
  const [haveTimm, setHaveTimm] = useState(false)
  useEffect(()=>{
    fetch('/api/items/tagger_capabilities/')
      .then(r=>r.json()).then(d=>setHaveTimm(!!d.have_timm)).catch(()=>{})
  }, [])

  useEffect(() => {
    fetch('/api/items/all_characters/').then(r => r.json()).then(d => { if (Array.isArray(d)) setAllChars(d) }).catch(() => {})
  }, [])

  // Sequentially requests a suggestion for every item in `targetItems` that
  // doesn't already have a cached one — see the former EditQueueManager's
  // own comment for why this is sequential, not parallel.
  const runSuggestFor = useCallback(async (targetItems, mode, model) => {
    if(bulkSuggestingRef.current) return
    const targets = targetItems.filter(it => !suggestionsRef.current[it.id])
    if(targets.length === 0) return

    bulkSuggestingRef.current = true
    setBulkSuggesting(true)
    let done = 0, skipped = 0
    setBulkProgress({ done, total: targets.length, skipped })
    for(const it of targets){
      if(cancelledRef.current) return
      try{
        const resp = await fetch(`/api/items/${it.id}/suggest_tags/`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            external: mode === 'external',
            model: model === 'canary' ? 'timm' : 'default',
          }),
          signal: abortRef.current.signal,
        })
        if(cancelledRef.current) return
        const j = await resp.json().catch(()=>({}))
        if(cancelledRef.current) return
        if(resp.ok){
          suggestionsRef.current = { ...suggestionsRef.current, [it.id]: j }
          setSuggestions(suggestionsRef.current)
        } else {
          skipped++
        }
      }catch(e){
        if(cancelledRef.current || (e && e.name === 'AbortError')) return
        console.error('Suggest failed for item', it.id, e)
        skipped++
      }
      done++
      setBulkProgress({ done, total: targets.length, skipped })
    }
    bulkSuggestingRef.current = false
    setBulkSuggesting(false)
  }, [])

  const load = useCallback(async () => {
    // allItems-scoped mode: scopedItems is already fully loaded data (no
    // request needed) — filter it client-side by the same predicate the
    // corresponding server-side action uses.
    if(Array.isArray(scopedItems)){
      const list = mode === 'mismatch'
        ? scopedItems.filter(isMismatched)
        : scopedItems.filter(it => isNeedsAttention(it, activeFields))
      setItems(list)
      setSelectedId(prev => (prev != null && list.some(it => it.id === prev)) ? prev : null)
      setCount(list.length)
      setHasMore(false)
      setNextBeforeId(null)
      return
    }

    setLoading(true)
    try{
      let url
      if(mode === 'mismatch'){
        url = '/api/items/region_mismatch_queue/'
      } else {
        const missing = Array.from(activeFields).join(',') || MISSING_FIELDS.map(f=>f.key).join(',')
        url = `/api/items/needs_attention_queue/?missing=${encodeURIComponent(missing)}`
      }
      const r = await fetch(url)
      const data = await r.json().catch(()=>({}))
      const list = data.results || []
      setItems(list)
      setSelectedId(prev => (prev != null && list.some(it => it.id === prev)) ? prev : null)
      setCount(data.count ?? list.length)
      setHasMore(!!data.has_more)
      setNextBeforeId(data.next_before_id ?? null)
    }catch(e){
      console.error('Failed to load item queue', e)
      setItems([]); setSelectedId(null); setCount(0); setHasMore(false); setNextBeforeId(null)
    }finally{
      setLoading(false)
    }
  }, [activeFields, scopedItems, mode])

  useEffect(()=>{ load() }, [load])

  useEffect(() => {
    function onPreviewUpdated(ev) {
      const id = ev && ev.detail && ev.detail.id
      if (id == null) return
      if (suggestionsRef.current[id]) {
        const nextSug = { ...suggestionsRef.current }
        delete nextSug[id]
        suggestionsRef.current = nextSug
        setSuggestions(nextSug)
      }
      fetch(`/api/items/${id}/`, { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .then(fresh => {
          if (!fresh) return
          setItems(prev => prev.map(it => it.id === fresh.id ? { ...it, ...fresh } : it))
        })
        .catch(() => {})
    }
    function onItemUpdated(ev) {
      const detail = ev && ev.detail
      if (!detail || detail.id == null) return
      if (detail.item) {
        setItems(prev => prev.map(it => it.id === detail.id ? { ...it, ...detail.item } : it))
      }
    }
    function onItemDeleted(ev) {
      const id = ev && ev.detail && ev.detail.id
      if (id == null) return
      setItems(prev => {
        if (!prev.some(it => it.id === id)) return prev
        const rest = prev.filter(it => it.id !== id)
        setSelectedId(sel => sel === id ? null : sel)
        setCount(c => Math.max(0, c - 1))
        return rest
      })
    }
    window.addEventListener('item-preview-updated', onPreviewUpdated)
    window.addEventListener('item-updated', onItemUpdated)
    window.addEventListener('item-deleted', onItemDeleted)
    return () => {
      window.removeEventListener('item-preview-updated', onPreviewUpdated)
      window.removeEventListener('item-updated', onItemUpdated)
      window.removeEventListener('item-deleted', onItemDeleted)
    }
  }, [])

  // Standalone-only (see `load`). Same before_id id-cutoff cursor as the two
  // former queue managers — see their own comments for why (a page/offset
  // cursor shrinks out from under itself as items get resolved and drop out
  // of the filter, silently skipping a batch).
  async function loadMore(){
    if(!hasMore || nextBeforeId == null || loadingMore) return
    setLoadingMore(true)
    try{
      let url
      if(mode === 'mismatch'){
        url = `/api/items/region_mismatch_queue/?before_id=${nextBeforeId}`
      } else {
        const missing = Array.from(activeFields).join(',') || MISSING_FIELDS.map(f=>f.key).join(',')
        url = `/api/items/needs_attention_queue/?missing=${encodeURIComponent(missing)}&before_id=${nextBeforeId}`
      }
      const r = await fetch(url)
      const data = await r.json().catch(()=>({}))
      const list = data.results || []
      setItems(prev => [...prev, ...list])
      setHasMore(!!data.has_more)
      setNextBeforeId(data.next_before_id ?? null)
    }catch(e){
      console.error('Failed to load more item queue items', e)
    }finally{
      setLoadingMore(false)
    }
  }

  function toggleField(key){
    setActiveFields(prev => {
      const next = new Set(prev)
      if(next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  function selectNext(fromId){
    setItems(prev => {
      const idx = prev.findIndex(it => it.id === fromId)
      const rest = prev.filter(it => it.id !== fromId)
      const nextItem = rest[Math.min(idx, rest.length - 1)]
      setSelectedId(nextItem ? nextItem.id : null)
      return rest
    })
    setCount(c => Math.max(0, c - 1))
  }

  function removeItems(ids){
    const idSet = new Set(ids)
    setItems(prev => {
      const rest = prev.filter(it => !idSet.has(it.id))
      setSelectedId(sel => idSet.has(sel) ? null : sel)
      return rest
    })
    setCount(c => Math.max(0, c - idSet.size))
  }

  const selected = items.find(it => it.id === selectedId) || null

  // Reset per-item UI state whenever selection changes (mismatch-tab-only
  // state — ItemQueuePanel resets its own attention-tab state internally by
  // being keyed on selected.id, see below).
  useEffect(() => {
    setCharList(selected ? (selected.characters || []) : [])
    setRegionDirty(false)
    setAnnotating(false)
    setItemPanelDirty(false)
  }, [selected && selected.id])

  // Guard for anything that would throw away unsaved work: skipping to
  // another item, switching mode tabs, changing page, or closing the panel.
  function confirmDiscardIfDirty() {
    const dirty = mode === 'mismatch' ? regionDirty : itemPanelDirty
    if (!dirty) return true
    return window.confirm('保存されていない変更があります。破棄しますか？')
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
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
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
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
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
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
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

  // Items where titles/characters/situation are all already filled and tags
  // is the ONLY thing missing, with a cached tag suggestion available — see
  // the former EditQueueManager's own comment.
  const tagsOnlyReady = mode === 'attention' ? items.filter(it => (
    !isFieldMissing(it, 'titles') &&
    !isFieldMissing(it, 'characters') &&
    !isFieldMissing(it, 'situation') &&
    isFieldMissing(it, 'tags') &&
    (suggestions[it.id]?.tags?.length > 0)
  )) : []

  async function bulkSaveTagsOnly(){
    if(bulkSaving || tagsOnlyReady.length === 0) return
    const targets = tagsOnlyReady
    if(!window.confirm(`タグ提案がある${targets.length}件に、提案されたタグをそのまま保存します。よろしいですか？`)) return

    setBulkSaving(true)
    let done = 0, failed = 0
    setBulkSaveProgress({ done, total: targets.length, failed })
    const savedIds = []
    for(const it of targets){
      if(cancelledRef.current) return
      try{
        const payload = {
          titles: it.titles || [],
          characters: it.characters || [],
          situation: it.situation || '',
          tags: suggestions[it.id].tags.map(t => t.name),
          artist: it.artist || '',
        }
        const resp = await fetch(`/api/items/${it.id}/update_fields/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
          signal: abortRef.current.signal,
        })
        if(cancelledRef.current) return
        const j = await resp.json().catch(()=>({}))
        if(cancelledRef.current) return
        if(resp.ok){
          savedIds.push(it.id)
          notify('item-updated', { id: it.id, item: j.item })
        } else {
          failed++
        }
      }catch(e){
        if(cancelledRef.current || (e && e.name === 'AbortError')) return
        console.error('Bulk tag save failed for item', it.id, e)
        failed++
      }
      done++
      setBulkSaveProgress({ done, total: targets.length, failed })
    }
    if(savedIds.length > 0) removeItems(savedIds)
    setBulkSaving(false)
  }

  const content = (
    <>
      <div className="cgm-panel-header" style={{ position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <strong>編集キュー ({count}件)</strong>
          <span style={{
            fontSize: 11,
            padding: '2px 7px',
            borderRadius: 999,
            background: mode === 'mismatch' ? 'rgba(239,68,68,0.18)' : 'var(--active-bg)',
            color: mode === 'mismatch' ? '#fca5a5' : 'var(--active-text)',
            fontWeight: 600,
          }}>
            {mode === 'mismatch' ? '不整合あり' : '要対応'}
          </span>
          {bulkSuggesting && (
            <span style={{ fontSize: 11, color: '#93c5fd' }}>
              提案中 ({bulkProgress ? bulkProgress.done : 0}/{bulkProgress ? bulkProgress.total : 0})
            </span>
          )}
          {bulkSaving && (
            <span style={{ fontSize: 11, color: '#93c5fd' }}>
              一括保存中 ({bulkSaveProgress ? bulkSaveProgress.done : 0}/{bulkSaveProgress ? bulkSaveProgress.total : 0})
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {onPopOut && (
            <button className="btn" style={{ fontSize: 12 }} onClick={() => { if (confirmDiscardIfDirty()) onPopOut() }} title="今表示中のページのキューを保ったまま、別ウィンドウで開きます">
              別ウィンドウで開く
            </button>
          )}
          <div ref={settingsMenuRef} style={{ position: 'relative' }}>
            <button
              type="button"
              className="btn"
              onClick={() => setSettingsMenuOpen(v => !v)}
              aria-expanded={settingsMenuOpen}
              title="モード・対象項目・提案設定"
              style={{
                fontSize: 15,
                lineHeight: 1,
                padding: '5px 9px',
                background: settingsMenuOpen ? 'var(--active-bg)' : undefined,
              }}
            >
              ☰
            </button>
            {settingsMenuOpen && (
              <div style={{
                position: 'absolute',
                top: 'calc(100% + 6px)',
                right: 0,
                zIndex: 60,
                width: 360,
                maxWidth: 'calc(100vw - 32px)',
                maxHeight: '75vh',
                overflowY: 'auto',
                background: '#0f172a',
                border: '1px solid #334155',
                borderRadius: 8,
                padding: 12,
                boxShadow: '0 12px 32px rgba(0,0,0,0.6)',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                fontSize: 12,
                color: '#e2e8f0',
              }}>
                <div>
                  <div style={{ fontWeight: 700, marginBottom: 6, color: '#94a3b8' }}>キューモード</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      type="button"
                      className="btn"
                      style={{
                        flex: 1, fontSize: 12,
                        fontWeight: mode === 'attention' ? 700 : 400,
                        background: mode === 'attention' ? 'var(--active-bg)' : undefined,
                      }}
                      onClick={() => { changeMode('attention'); setSettingsMenuOpen(false) }}
                    >要対応</button>
                    <button
                      type="button"
                      className="btn"
                      style={{
                        flex: 1, fontSize: 12,
                        fontWeight: mode === 'mismatch' ? 700 : 400,
                        background: mode === 'mismatch' ? 'rgba(239,68,68,0.2)' : undefined,
                      }}
                      onClick={() => { changeMode('mismatch'); setSettingsMenuOpen(false) }}
                    >不整合あり</button>
                  </div>
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, lineHeight: 1.45 }}>
                    {mode === 'mismatch'
                      ? '領域ラベルと編集キューのキャラ一覧が一致していないアイテムです。'
                      : '対象項目が未設定、または(SOLO以外で)領域ラベル未保存のアイテムです。'}
                  </div>
                </div>

                {mode === 'attention' && (
                  <>
                    <div style={{ borderTop: '1px solid #1e293b', paddingTop: 10 }}>
                      <div style={{ fontWeight: 700, marginBottom: 6, color: '#94a3b8' }}>未設定とみなす項目</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                        {MISSING_FIELDS.map(f => (
                          <label key={f.key} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                            <input type="checkbox" checked={activeFields.has(f.key)} onChange={() => toggleField(f.key)} />
                            <span>{f.icon} {f.label}</span>
                          </label>
                        ))}
                      </div>
                    </div>

                    {(tagsOnlyReady.length > 0 || bulkSaving) && (
                      <div style={{ borderTop: '1px solid #1e293b', paddingTop: 10 }}>
                        <button
                          type="button"
                          className="btn"
                          style={{ width: '100%', fontSize: 12 }}
                          onClick={bulkSaveTagsOnly}
                          disabled={bulkSaving || tagsOnlyReady.length === 0}
                        >
                          {bulkSaving
                            ? `💾 タグを一括保存中… (${bulkSaveProgress ? bulkSaveProgress.done : 0}/${bulkSaveProgress ? bulkSaveProgress.total : 0})`
                            : `タグのみ不足の${tagsOnlyReady.length}件を一括保存`}
                        </button>
                        {!bulkSaving && bulkSaveProgress && bulkSaveProgress.failed > 0 && (
                          <div style={{ fontSize: 11, color: '#f87171', marginTop: 4 }}>({bulkSaveProgress.failed}件失敗)</div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
          <button className="cgm-panel-close" onClick={handleClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
        </div>
      </div>

      {((Array.isArray(allItems) && queuePageCount > 1) || mode === 'attention') && (
        <div
          className="cgm-panel-search"
          style={{
            padding: '4px 12px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 10,
            flexWrap: 'wrap',
          }}
        >
          <div>
            {Array.isArray(allItems) && queuePageCount > 1 && (
              <Pagination
                page={queuePageIndex}
                totalPages={queuePageCount}
                onGoToPage={(p) => { if (confirmDiscardIfDirty()) setQueuePageIndex(p) }}
                onPrev={() => { if (confirmDiscardIfDirty()) setQueuePageIndex(p => Math.max(0, p - 1)) }}
                onNext={() => { if (confirmDiscardIfDirty()) setQueuePageIndex(p => Math.min(queuePageCount - 1, p + 1)) }}
                prevDisabled={queuePageIndex === 0}
                nextDisabled={queuePageIndex >= queuePageCount - 1}
                resultsLabel={`全${allItems.length}件`}
              />
            )}
          </div>
          {mode === 'attention' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginLeft: 'auto' }}>
              {haveTimm && (
                <select
                  value={suggestModel}
                  onChange={e => {
                    setSuggestModel(e.target.value)
                    suggestionsRef.current = {}
                    setSuggestions({})
                    setBulkProgress(null)
                  }}
                  disabled={bulkSuggesting}
                  style={{ fontSize: 12 }}
                  title="画像解析モデル"
                >
                  <option value="default">標準(軽量・高速)</option>
                  <option value="canary">2026年学習の最新モデル(重い)</option>
                </select>
              )}
              <button
                type="button"
                className="btn"
                style={{ fontSize: 12, background: '#2563eb', color: '#fff', padding: '4px 10px' }}
                onClick={() => {
                  if (suggestModel === 'canary' && !window.confirm('最新モデルは初回選択時にサーバー側で大きいモデル(約1.3GB)をダウンロードします。時間がかかる場合があります。続行しますか？')) return
                  runSuggestFor(items, suggestMode, suggestModel)
                }}
                disabled={bulkSuggesting || items.length === 0 || items.every(it => suggestions[it.id])}
              >
                {bulkSuggesting
                  ? `提案中… (${bulkProgress ? bulkProgress.done : 0}/${bulkProgress ? bulkProgress.total : 0})`
                  : '提案を開始'}
              </button>
              {!bulkSuggesting && bulkProgress && bulkProgress.skipped > 0 && (
                <span style={{ fontSize: 11, color: '#f87171' }}>({bulkProgress.skipped}件失敗/スキップ)</span>
              )}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', minHeight: 0, flex: '1 1 auto' }}>
        <div style={{ width: 72, borderRight: '1px solid var(--divider)', overflowY: 'auto', flexShrink: 0 }}>
          {loading && <div className="cgm-empty-hint" style={{ padding: 8, fontSize: 11 }}>読込中…</div>}
          {!loading && items.length === 0 && (
            <div className="cgm-empty-hint" style={{ padding: 8, fontSize: 11 }}>なし 🎉</div>
          )}
          {items.map(it => {
            if(mode === 'mismatch'){
              const diff = 'region_only_characters' in it
                ? { regionOnly: it.region_only_characters || [], itemOnly: it.item_only_characters || [] }
                : characterDiff(it)
              return (
                <div
                  key={it.id}
                  onClick={() => selectItem(it.id)}
                  title={`#${it.id} (${it.situation || '—'})`}
                  style={{
                    padding: '6px 4px', cursor: 'pointer',
                    background: it.id === selectedId ? 'var(--active-bg)' : 'transparent',
                    borderBottom: '1px solid var(--divider)',
                  }}
                >
                  <div style={{ fontSize: 11, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>#{it.id}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, marginTop: 2, fontSize: 11, lineHeight: 1.2 }}>
                    {diff.regionOnly.length > 0 && (
                      <span title={`領域のみ: ${diff.regionOnly.join(', ')}`}>🟥</span>
                    )}
                    {diff.itemOnly.length > 0 && (
                      <span title={`編集キューのみ: ${diff.itemOnly.join(', ')}`}>🟨</span>
                    )}
                  </div>
                </div>
              )
            }

            const { missingFields, needsRegion } = attentionReasons(it, activeFields)
            const missingMeta = MISSING_FIELDS.filter(f => missingFields.includes(f.key))
            return (
              <div
                key={it.id}
                onClick={()=>selectItem(it.id)}
                title={`#${it.id}`}
                style={{
                  padding:'6px 4px', cursor:'pointer',
                  background: it.id===selectedId ? 'var(--active-bg)' : 'transparent',
                  borderBottom:'1px solid var(--divider)',
                }}
              >
                <div style={{fontSize:11, fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                  #{it.id}
                </div>
                <div style={{ display:'flex', flexWrap:'wrap', gap:2, marginTop:2, fontSize:11, lineHeight:1.2 }}>
                  {missingMeta.map(f => (
                    <span key={f.key} title={`${f.label}不足`}>{f.icon}</span>
                  ))}
                  {needsRegion && (
                    <span title="領域未設定">🔲</span>
                  )}
                  {suggestions[it.id] && suggestions[it.id].source && suggestions[it.id].source !== 'none' && (
                    <span
                      title={
                        (suggestions[it.id].source === 'db' ? '提案あり（既存データから）'
                        : suggestions[it.id].source === 'tagger' ? '提案あり（画像解析から）'
                        : '提案あり（既存データ＋画像解析）')
                        + (suggestions[it.id].source.includes('danbooru') ? '・Danbooru照合で新規タイトル推論' : '')
                      }
                    >
                      {suggestions[it.id].source === 'db' ? '📚' : suggestions[it.id].source === 'tagger' ? '🏷' : '📚🏷'}
                      {suggestions[it.id].source.includes('danbooru') && '🌐'}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
          {hasMore && (
            <button className="btn" style={{ width: '100%', margin: '6px 0', fontSize: 10, padding: '4px 2px' }} onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? '…' : 'もっと'}
            </button>
          )}
        </div>

        <div style={mode === 'attention'
          ? { flex: 1, padding: 10, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }
          : { flex: 1, padding: 16, overflowY: 'auto' }
        }>
          {!selected ? (
            <div className="cgm-empty-hint">左のリストから項目を選んでください</div>
          ) : mode === 'attention' ? (
            <div style={{background:'#1e293b', borderRadius:8, padding:'10px 14px', flex:'1 1 auto', minHeight:0, display:'flex', flexDirection:'column'}}>
              <ItemQueuePanel
                key={selected.id}
                item={selected}
                closeLabel="スキップ（後で対応）"
                initialSuggestion={suggestions[selected.id] || null}
                compactQueueLayout={true}
                onDirtyChange={setItemPanelDirty}
                onClose={()=>selectNext(selected.id)}
                onSaved={(newItem)=>{
                  notify('item-updated', { id: selected.id, item: newItem })
                  selectNext(selected.id)
                }}
              />
            </div>
          ) : !annotating ? (
            (() => {
              const regionChars = [...regionCharsOf(selected)].sort()
              const itemChars = [...(selected.characters || [])].sort()
              return (
                <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                      <SourceLink item={selected} />
                    </span>
                    <button className="btn" style={{ padding: '4px 10px' }} onClick={skipCurrent}>スキップ（後で対応）</button>
                  </div>

                  <img
                    src={selected.first_preview_id != null ? `/api/items/${selected.id}/preview/?id=${selected.first_preview_id}` : `/api/items/${selected.id}/preview/`}
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
                <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: 16 }}>Item #{selected.id}</span>
                  <SourceLink item={selected} />
                </span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn" style={{ padding: '4px 10px' }} onClick={() => { if (confirmDiscardIfDirty()) setAnnotating(false) }}>
                    一覧表示に戻る
                  </button>
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

  if(standalone){
    return <div className="cgm-panel" style={{width:'100%', height:'100vh', maxHeight:'100vh', borderRadius:0}}>{content}</div>
  }

  // App.jsx keeps this component mounted across close/reopen so its state
  // (selected item, in-progress edits) survives — `hidden` just controls
  // visibility, not whether it's in the tree at all. An inline style (not
  // the plain `hidden` attribute) because it must beat .cgm-panel-backdrop's
  // own `display:flex` of equal selector specificity, which the `hidden`
  // attribute's UA-stylesheet default can't do on its own.
  return (
    <div className="cgm-panel-backdrop" style={hidden ? {display:'none'} : undefined} onClick={handleClose}>
      <div className="cgm-panel" style={{width:'98vw', maxWidth:'98vw', height:'96vh', maxHeight:'96vh'}} onClick={e=>e.stopPropagation()}>{content}</div>
    </div>
  )
}
