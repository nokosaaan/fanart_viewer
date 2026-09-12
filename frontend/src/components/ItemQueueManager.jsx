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
  { key: 'titles', label: 'タイトル' },
  { key: 'characters', label: 'キャラクター' },
  { key: 'tags', label: 'タグ' },
  { key: 'situation', label: 'シチュエーション' },
  { key: 'artist', label: '作者' },
]

function isFieldMissing(it, field){
  if (field === 'situation' || field === 'artist') return !it[field]
  const v = it[field]
  return !Array.isArray(v) || v.length === 0
}

// Mirrors ItemViewSet.region_label_queue's server-side eligibility filter
// (see needs_attention_queue's own OR of this exact condition with the
// missing-fields one below) — situation SOLO/R18 has nothing to
// disambiguate, and an item that's already been touched at all
// (character_regions non-empty) belongs to the 'mismatch' tab from then on,
// never back here.
function isEligibleForRegion(it){
  if (it.situation === 'SOLO' || it.situation === 'R18') return false
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

  const [suggestMode, setSuggestMode] = useState('local')
  const [suggestModel, setSuggestModel] = useState('default')
  const [haveTimm, setHaveTimm] = useState(false)
  const [suggestUseEnsemble, setSuggestUseEnsemble] = useState(true)
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
  const runSuggestFor = useCallback(async (targetItems, mode, model, useEnsemble) => {
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
            use_ensemble: !!useEnsemble,
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
    setSelectedId(null)

    // allItems-scoped mode: scopedItems is already fully loaded data (no
    // request needed) — filter it client-side by the same predicate the
    // corresponding server-side action uses.
    if(Array.isArray(scopedItems)){
      const list = mode === 'mismatch'
        ? scopedItems.filter(isMismatched)
        : scopedItems.filter(it => isNeedsAttention(it, activeFields))
      setItems(list)
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
      setCount(data.count ?? list.length)
      setHasMore(!!data.has_more)
      setNextBeforeId(data.next_before_id ?? null)
    }catch(e){
      console.error('Failed to load item queue', e)
      setItems([]); setCount(0); setHasMore(false); setNextBeforeId(null)
    }finally{
      setLoading(false)
    }
  }, [activeFields, scopedItems, mode])

  useEffect(()=>{ load() }, [load])

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
      <div className="cgm-panel-header">
        <strong>編集キュー ({count}件)</strong>
        <div style={{display:'flex', alignItems:'center', gap:8}}>
          {onPopOut && (
            <button className="btn" style={{fontSize:12}} onClick={() => { if (confirmDiscardIfDirty()) onPopOut() }} title="今表示中のページのキューを保ったまま、別ウィンドウで開きます">
              別ウィンドウで開く
            </button>
          )}
          <button className="cgm-panel-close" onClick={handleClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
        </div>
      </div>

      {Array.isArray(allItems) && queuePageCount > 1 && (
        <div className="cgm-panel-search">
          <Pagination
            page={queuePageIndex}
            totalPages={queuePageCount}
            onGoToPage={(p) => { if (confirmDiscardIfDirty()) setQueuePageIndex(p) }}
            onPrev={() => { if (confirmDiscardIfDirty()) setQueuePageIndex(p => Math.max(0, p - 1)) }}
            onNext={() => { if (confirmDiscardIfDirty()) setQueuePageIndex(p => Math.min(queuePageCount - 1, p + 1)) }}
            prevDisabled={queuePageIndex === 0}
            nextDisabled={queuePageIndex >= queuePageCount - 1}
            resultsLabel={`対象ページ ${queuePageIndex + 1}/${queuePageCount}(一覧側の全${allItems.length}件のうち、このページの対象分のみ表示中)`}
          />
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, padding: '8px 12px 0' }}>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'attention' ? 700 : 400, background: mode === 'attention' ? '#eff6ff' : undefined }}
          onClick={() => changeMode('attention')}
        >要対応</button>
        <button
          className="btn"
          style={{ fontSize: 12, fontWeight: mode === 'mismatch' ? 700 : 400, background: mode === 'mismatch' ? '#fef2f2' : undefined }}
          onClick={() => changeMode('mismatch')}
        >不整合あり</button>
      </div>

      <div className="cgm-panel-search" style={{ fontSize: 12, color: '#6b7280' }}>
        {mode === 'mismatch'
          ? '一度は領域ラベルを保存したものの、編集キューのキャラ一覧と完全には一致していないアイテムです。両方の一覧を見比べて、どちらを採用するか・そのままでよいか・ここで矩形を追加して解決するかを選んでください。'
          : 'タイトル・キャラ・タグ・シチュエーション・作者のいずれかが未設定、または(SOLO・R18以外で)まだ領域ラベルを一度も保存していないアイテムが対象です。フィールドの編集と領域ラベル付けを1つの保存ボタンでまとめて行えます。'}
      </div>

      {mode === 'attention' && (
        <div className="cgm-panel-search" style={{display:'flex', flexWrap:'wrap', alignItems:'center', gap:14}}>
          <span style={{fontSize:12, color:'#6b7280'}}>未設定とみなす項目:</span>
          {MISSING_FIELDS.map(f => (
            <label key={f.key} style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer'}}>
              <input type="checkbox" checked={activeFields.has(f.key)} onChange={()=>toggleField(f.key)} />
              {f.label}
            </label>
          ))}
        </div>
      )}

      {mode === 'attention' && (
        <div className="cgm-panel-search" style={{display:'flex', flexWrap:'wrap', alignItems:'center', gap:10}}>
          <span style={{fontSize:12, color:'#6b7280'}}>提案モード:</span>
          <label style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer'}}>
            <input type="radio" checked={suggestMode==='local'} onChange={()=>setSuggestMode('local')} disabled={bulkSuggesting} />
            ローカルのみ
          </label>
          <label style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer'}}>
            <input type="radio" checked={suggestMode==='external'} onChange={()=>setSuggestMode('external')} disabled={bulkSuggesting} />
            Danbooru照合あり(外部通信・低速)
          </label>
          {haveTimm && (
            <>
              <span style={{fontSize:12, color:'#6b7280', marginLeft:8}}>画像解析モデル:</span>
              <select value={suggestModel} onChange={e=>setSuggestModel(e.target.value)} disabled={bulkSuggesting} style={{fontSize:12}}>
                <option value="default">標準(軽量・高速)</option>
                <option value="canary">2026年学習の最新モデル(重い・初回は大きいダウンロード)</option>
              </select>
            </>
          )}
          <label style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer', marginLeft:8}} title="複数の情報源を重み付けして統合する新方式(実験的)。従来方式より実データでキャラ推定精度が高いことを確認済み">
            <input type="checkbox" checked={suggestUseEnsemble} onChange={e=>setSuggestUseEnsemble(e.target.checked)} disabled={bulkSuggesting} />
            統合型の推論を使う(実験的)
          </label>
          <button className="btn" style={{fontSize:12}} onClick={()=>{
              if(suggestModel==='canary' && !window.confirm('最新モデルは初回選択時にサーバー側で大きいモデル(約1.3GB)をダウンロードします。時間がかかる場合があります。続行しますか？')) return
              runSuggestFor(items, suggestMode, suggestModel, suggestUseEnsemble)
            }} disabled={bulkSuggesting || items.length===0 || items.every(it => suggestions[it.id])}>
            {bulkSuggesting
              ? `提案中… (${bulkProgress ? bulkProgress.done : 0}/${bulkProgress ? bulkProgress.total : 0})`
              : '提案を開始'}
          </button>
          {!bulkSuggesting && bulkProgress && bulkProgress.skipped > 0 && (
            <span style={{fontSize:11, color:'#dc2626'}}>({bulkProgress.skipped}件失敗/スキップ)</span>
          )}
        </div>
      )}
      {mode === 'attention' && !bulkSuggesting && !bulkProgress && (
        <div className="cgm-panel-search" style={{fontSize:11, color:'#6b7280'}}>
          モードを選んで「提案を開始」を押すまで、AI提案(DBの傾向・必要なら画像解析)は実行されません。
        </div>
      )}

      {mode === 'attention' && (tagsOnlyReady.length > 0 || bulkSaving) && (
        <div className="cgm-panel-search" style={{display:'flex', alignItems:'center', gap:10}}>
          <span style={{fontSize:12, color: bulkSaving ? '#2563eb' : '#6b7280'}}>
            {bulkSaving
              ? `💾 タグを一括保存中… (${bulkSaveProgress ? bulkSaveProgress.done : 0}/${bulkSaveProgress ? bulkSaveProgress.total : 0})`
              : `タグ以外は入力済み・タグ提案ありの項目が${tagsOnlyReady.length}件あります`}
          </span>
          <button className="btn" style={{fontSize:12}} onClick={bulkSaveTagsOnly} disabled={bulkSaving || tagsOnlyReady.length === 0}>
            タグのみ不足の{tagsOnlyReady.length}件を一括保存
          </button>
          {!bulkSaving && bulkSaveProgress && bulkSaveProgress.failed > 0 && (
            <span style={{fontSize:11, color:'#dc2626'}}>({bulkSaveProgress.failed}件失敗)</span>
          )}
        </div>
      )}

      <div style={{ display: 'flex', minHeight: 0, flex: '1 1 auto' }}>
        <div style={{ width: 260, borderRight: '1px solid #f3f4f6', overflowY: 'auto', flexShrink: 0 }}>
          {loading && <div className="cgm-empty-hint" style={{ padding: 12 }}>読み込み中…</div>}
          {!loading && items.length === 0 && (
            <div className="cgm-empty-hint" style={{ padding: 12 }}>該当するアイテムはありません 🎉</div>
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
                  {diff.regionOnly.length > 0 && (
                    <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>
                      領域のみ: {diff.regionOnly.join(', ')}
                    </div>
                  )}
                  {diff.itemOnly.length > 0 && (
                    <div style={{ fontSize: 11, color: '#d97706', marginTop: 2 }}>
                      編集キューのみ: {diff.itemOnly.join(', ')}
                    </div>
                  )}
                </div>
              )
            }

            const { missingFields, needsRegion } = attentionReasons(it, activeFields)
            const missingLabels = MISSING_FIELDS.filter(f => missingFields.includes(f.key)).map(f => f.label)
            return (
              <div
                key={it.id}
                onClick={()=>selectItem(it.id)}
                style={{
                  padding:'10px 12px', cursor:'pointer',
                  background: it.id===selectedId ? '#eff6ff' : 'transparent',
                  borderBottom:'1px solid #f3f4f6',
                }}
              >
                <div style={{fontSize:13, fontWeight:600}}>
                  #{it.id}
                  {suggestions[it.id] && (
                    suggestions[it.id].source && suggestions[it.id].source !== 'none' ? (
                      <span
                        title={
                          (suggestions[it.id].source === 'db' ? '提案あり（既存データから）'
                          : suggestions[it.id].source === 'tagger' ? '提案あり（画像解析から）'
                          : '提案あり（既存データ＋画像解析）')
                          + (suggestions[it.id].source.includes('danbooru') ? '・Danbooru照合で新規タイトル推論' : '')
                        }
                        style={{marginLeft:6}}
                      >
                        {suggestions[it.id].source === 'db' ? '📚' : suggestions[it.id].source === 'tagger' ? '🏷' : '📚🏷'}
                        {suggestions[it.id].source.includes('danbooru') && '🌐'}
                      </span>
                    ) : (
                      <span title="確認済み・提案なし" style={{marginLeft:6, color:'#d1d5db', fontWeight:400}}>·</span>
                    )
                  )}
                </div>
                <div style={{fontSize:12, color:'#6b7280'}}>
                  {missingLabels.length > 0 && <>不足: {missingLabels.join('・')}</>}
                  {missingLabels.length === 0 && !needsRegion && '—'}
                </div>
                {needsRegion && (
                  <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 2 }}>領域未設定</div>
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
          ) : mode === 'attention' ? (
            <div style={{background:'#1e293b', borderRadius:8, padding:'16px 20px'}}>
              <ItemQueuePanel
                key={selected.id}
                item={selected}
                closeLabel="スキップ（後で対応）"
                initialSuggestion={suggestions[selected.id] || null}
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
      <div className="cgm-panel" style={{width:1000}} onClick={e=>e.stopPropagation()}>{content}</div>
    </div>
  )
}
