import React, { useState, useEffect, useCallback, useRef } from 'react'
import { ItemEditForm } from './EditFields'
import { notify } from '../lib/crossWindowSync'

const MISSING_FIELDS = [
  { key: 'titles', label: 'タイトル' },
  { key: 'characters', label: 'キャラクター' },
  { key: 'tags', label: 'タグ' },
  { key: 'situation', label: 'シチュエーション' },
]

function isFieldMissing(it, field){
  if (field === 'situation') return !it.situation
  const v = it[field]
  return !Array.isArray(v) || v.length === 0
}

function summarizeMissing(it){
  const missing = []
  if (isFieldMissing(it, 'titles')) missing.push('タイトル')
  if (isFieldMissing(it, 'characters')) missing.push('キャラ')
  if (isFieldMissing(it, 'tags')) missing.push('タグ')
  if (isFieldMissing(it, 'situation')) missing.push('状況')
  return missing.join('・') || '—'
}

// Mailbox-style bulk review of items missing metadata, instead of opening
// EditFields one row at a time from the main list. Reuses the same edit
// form (ItemEditForm) embedded in a right-hand detail pane; the left
// sidebar is the queue of items still needing attention.
// `standalone`: rendered as a popped-out browser window (see HeaderMenu's
// "別ウィンドウで開く" / StandaloneEditQueue in App.jsx) instead of an
// overlay in the main window. Fills the viewport instead of floating as a
// centered panel; onSaved broadcasts via BroadcastChannel (crossWindowSync)
// so the main window's item list stays live-synced either way.
// `currentPageItems`: the main list's currently-displayed page (App.jsx's
// paginatedItems) — lets the "現在のページのみ対象" toggle below scope the
// queue to just that, mirroring FetchQueueManager's page-scoped bulk-fetch
// button. Not available in standalone mode (a popped-out window has no
// access to the main window's pagination state), so the toggle is hidden
// there.
export default function EditQueueManager({ onClose, standalone = false, currentPageItems = null }){
  const [activeFields, setActiveFields] = useState(() => new Set(MISSING_FIELDS.map(f => f.key)))
  const [currentPageOnly, setCurrentPageOnly] = useState(false)
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const [nextUrl, setNextUrl] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  // itemId -> suggestion result. Populated by runSuggestFor so opening an
  // item later applies it instantly instead of waiting on a fresh request
  // (see ItemEditForm's initialSuggestion prop). Mirrored into a ref so
  // runSuggestFor can check "already have this one" without needing
  // `suggestions` in its own dependency list (see bulkSuggestingRef below
  // for why that matters).
  const [suggestions, setSuggestions] = useState({})
  const suggestionsRef = useRef({})
  useEffect(()=>{ suggestionsRef.current = suggestions }, [suggestions])

  const [bulkSuggesting, setBulkSuggesting] = useState(false)
  // Guards runSuggestFor against overlapping runs. Deliberately a ref, not
  // just the `bulkSuggesting` state: if runSuggestFor closed over the state
  // value directly, calling setBulkSuggesting inside it would redefine the
  // function (were it in a useCallback dep list), which would redefine
  // `load` below (since load calls runSuggestFor), which would re-fire the
  // `useEffect(()=>{load()},[load])` mount effect — reloading the whole
  // queue mid-suggestion-run and resetting selectedId out from under
  // whatever the user was doing.
  const bulkSuggestingRef = useRef(false)
  const [bulkProgress, setBulkProgress] = useState(null) // {done, total, skipped}

  // Sequentially requests a suggestion for every item in `targetItems` that
  // doesn't already have a cached one, caching results as it goes.
  // Sequential (not parallel) on purpose: the DB-only case resolves near
  // instantly, but any item that falls back to the image tagger server-side
  // is a real ~5s+ CPU-bound call, and hammering several concurrently would
  // just contend for the same CPU on the Pi4 deployment target for no speed
  // gain.
  const runSuggestFor = useCallback(async (targetItems) => {
    if(bulkSuggestingRef.current) return
    const targets = targetItems.filter(it => !suggestionsRef.current[it.id])
    if(targets.length === 0) return

    bulkSuggestingRef.current = true
    setBulkSuggesting(true)
    let done = 0, skipped = 0
    setBulkProgress({ done, total: targets.length, skipped })
    for(const it of targets){
      try{
        const resp = await fetch(`/api/items/${it.id}/suggest_tags/`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
        const j = await resp.json().catch(()=>({}))
        if(resp.ok){
          suggestionsRef.current = { ...suggestionsRef.current, [it.id]: j }
          setSuggestions(suggestionsRef.current)
        } else {
          skipped++
        }
      }catch(e){
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

    // Current-page mode: currentPageItems is already fully loaded data (no
    // request needed), so just filter it client-side by the same
    // missing-field criteria the server-side `incomplete` action uses.
    if(currentPageOnly && currentPageItems){
      const list = currentPageItems.filter(it => Array.from(activeFields).some(f => isFieldMissing(it, f)))
      setItems(list)
      setCount(list.length)
      setNextUrl(null)
      runSuggestFor(list)
      return
    }

    setLoading(true)
    try{
      const missing = Array.from(activeFields).join(',') || MISSING_FIELDS.map(f=>f.key).join(',')
      const r = await fetch(`/api/items/incomplete/?missing=${encodeURIComponent(missing)}`)
      const data = await r.json().catch(()=>({}))
      const list = Array.isArray(data) ? data : (data.results || [])
      setItems(list)
      setCount(Array.isArray(data) ? list.length : (data.count ?? list.length))
      setNextUrl(Array.isArray(data) ? null : (data.next || null))
      runSuggestFor(list) // fire and forget — don't block the loading spinner on this
    }catch(e){
      console.error('Failed to load incomplete items', e)
      setItems([]); setCount(0); setNextUrl(null)
    }finally{
      setLoading(false)
    }
  }, [activeFields, runSuggestFor, currentPageOnly, currentPageItems])

  useEffect(()=>{ load() }, [load])

  async function loadMore(){
    if(!nextUrl || loadingMore) return
    setLoadingMore(true)
    try{
      let fetchUrl = nextUrl
      try{ const u = new URL(nextUrl); fetchUrl = u.pathname + (u.search || '') }catch(_){}
      const r = await fetch(fetchUrl)
      const data = await r.json().catch(()=>({}))
      const list = Array.isArray(data) ? data : (data.results || [])
      setItems(prev => [...prev, ...list])
      setNextUrl(Array.isArray(data) ? null : (data.next || null))
      runSuggestFor(list)
    }catch(e){
      console.error('Failed to load more incomplete items', e)
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
      // pick whichever item now sits at the same position (i.e. "the next one")
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
        <strong>編集キュー — 未設定アイテム ({count}件)</strong>
        <button className="cgm-panel-close" onClick={onClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
      </div>

      <div className="cgm-panel-search" style={{display:'flex', flexWrap:'wrap', alignItems:'center', gap:14}}>
          <span style={{fontSize:12, color:'#6b7280'}}>未設定とみなす項目:</span>
          {MISSING_FIELDS.map(f => (
            <label key={f.key} style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer'}}>
              <input type="checkbox" checked={activeFields.has(f.key)} onChange={()=>toggleField(f.key)} />
              {f.label}
            </label>
          ))}
          {!standalone && currentPageItems && (
            <label style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer', marginLeft:'auto'}}>
              <input type="checkbox" checked={currentPageOnly} onChange={()=>setCurrentPageOnly(v=>!v)} />
              現在のページのみ対象
            </label>
          )}
        </div>

        <div className="cgm-panel-search" style={{display:'flex', alignItems:'center', gap:10}}>
          <span style={{fontSize:12, color: bulkSuggesting ? '#2563eb' : '#6b7280'}}>
            {bulkSuggesting
              ? `🏷 自動提案を準備中… (${bulkProgress ? bulkProgress.done : 0}/${bulkProgress ? bulkProgress.total : 0})`
              : '🏷 各項目は開いた時点で提案(DBの傾向・必要なら画像解析)が反映済みの状態になります'}
          </span>
          <button className="btn" style={{fontSize:12}} onClick={()=>runSuggestFor(items)} disabled={bulkSuggesting || items.every(it => suggestions[it.id])}>
            未処理分を再提案
          </button>
          {!bulkSuggesting && bulkProgress && bulkProgress.skipped > 0 && (
            <span style={{fontSize:11, color:'#dc2626'}}>({bulkProgress.skipped}件失敗/スキップ)</span>
          )}
        </div>

        <div style={{display:'flex', minHeight:0, flex:'1 1 auto'}}>
          {/* Queue list (mailbox sidebar) */}
          <div style={{width:260, borderRight:'1px solid #f3f4f6', overflowY:'auto', flexShrink:0}}>
            {loading && <div className="cgm-empty-hint" style={{padding:12}}>読み込み中…</div>}
            {!loading && items.length === 0 && (
              <div className="cgm-empty-hint" style={{padding:12}}>該当するアイテムはありません 🎉</div>
            )}
            {items.map(it => (
              <div
                key={it.id}
                onClick={()=>setSelectedId(it.id)}
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
                          suggestions[it.id].source === 'db' ? '提案あり（既存データから）'
                          : suggestions[it.id].source === 'tagger' ? '提案あり（画像解析から）'
                          : '提案あり（既存データ＋画像解析）'
                        }
                        style={{marginLeft:6}}
                      >
                        {suggestions[it.id].source === 'db' ? '📚' : suggestions[it.id].source === 'tagger' ? '🏷' : '📚🏷'}
                      </span>
                    ) : (
                      <span title="確認済み・提案なし" style={{marginLeft:6, color:'#d1d5db', fontWeight:400}}>·</span>
                    )
                  )}
                </div>
                <div style={{fontSize:12, color:'#6b7280'}}>不足: {summarizeMissing(it)}</div>
              </div>
            ))}
            {nextUrl && (
              <button className="btn" style={{width:'100%', margin:'8px 0', fontSize:12}} onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? '読み込み中…' : 'もっと読み込む'}
              </button>
            )}
          </div>

          {/* Selected item's edit form */}
          <div style={{flex:1, padding:16, overflowY:'auto'}}>
            {!selected ? (
              <div className="cgm-empty-hint">左のリストから項目を選んでください</div>
            ) : (
              <div style={{background:'#1e293b', borderRadius:8, padding:'16px 20px'}}>
                <ItemEditForm
                  key={selected.id}
                  item={selected}
                  closeLabel="スキップ（後で対応）"
                  initialSuggestion={suggestions[selected.id] || null}
                  onClose={()=>selectNext(selected.id)}
                  onSaved={(newItem)=>{
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

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{width:900}} onClick={e=>e.stopPropagation()}>{content}</div>
    </div>
  )
}
