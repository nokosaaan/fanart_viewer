import React, { useState, useEffect, useCallback } from 'react'
import { ItemEditForm } from './EditFields'

const MISSING_FIELDS = [
  { key: 'titles', label: 'タイトル' },
  { key: 'characters', label: 'キャラクター' },
  { key: 'tags', label: 'タグ' },
  { key: 'situation', label: 'シチュエーション' },
]

function summarizeMissing(it){
  const missing = []
  if (!Array.isArray(it.titles) || it.titles.length === 0) missing.push('タイトル')
  if (!Array.isArray(it.characters) || it.characters.length === 0) missing.push('キャラ')
  if (!Array.isArray(it.tags) || it.tags.length === 0) missing.push('タグ')
  if (!it.situation) missing.push('状況')
  return missing.join('・') || '—'
}

// Mailbox-style bulk review of items missing metadata, instead of opening
// EditFields one row at a time from the main list. Reuses the same edit
// form (ItemEditForm) embedded in a right-hand detail pane; the left
// sidebar is the queue of items still needing attention.
export default function EditQueueManager({ onClose }){
  const [activeFields, setActiveFields] = useState(() => new Set(MISSING_FIELDS.map(f => f.key)))
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const [nextUrl, setNextUrl] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  // itemId -> tagger result. Populated by runBulkSuggest so opening an item
  // later applies it instantly instead of waiting ~5s+/image for a fresh
  // inference call (see ItemEditForm's initialSuggestion prop).
  const [suggestions, setSuggestions] = useState({})
  const [bulkSuggesting, setBulkSuggesting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState(null) // {done, total, skipped}

  const load = useCallback(async () => {
    setLoading(true)
    setSelectedId(null)
    try{
      const missing = Array.from(activeFields).join(',') || MISSING_FIELDS.map(f=>f.key).join(',')
      const r = await fetch(`/api/items/incomplete/?missing=${encodeURIComponent(missing)}`)
      const data = await r.json().catch(()=>({}))
      const list = Array.isArray(data) ? data : (data.results || [])
      setItems(list)
      setCount(Array.isArray(data) ? list.length : (data.count ?? list.length))
      setNextUrl(Array.isArray(data) ? null : (data.next || null))
    }catch(e){
      console.error('Failed to load incomplete items', e)
      setItems([]); setCount(0); setNextUrl(null)
    }finally{
      setLoading(false)
    }
  }, [activeFields])

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
    }catch(e){
      console.error('Failed to load more incomplete items', e)
    }finally{
      setLoadingMore(false)
    }
  }

  // Sequentially runs the tagger over every currently-loaded queue item that
  // has a preview image and no cached suggestion yet, caching results as it
  // goes. Sequential (not parallel) on purpose — each call is a real ~5s+
  // CPU-bound inference, and hammering it concurrently would just contend
  // for the same CPU on the Pi4 deployment target for no speed gain.
  async function runBulkSuggest(){
    if(bulkSuggesting) return
    const targets = items.filter(it => it.has_preview && !suggestions[it.id])
    if(targets.length === 0) return
    setBulkSuggesting(true)
    let done = 0, skipped = 0
    setBulkProgress({ done, total: targets.length, skipped })
    for(const it of targets){
      try{
        const resp = await fetch(`/api/items/${it.id}/suggest_tags/`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
        const j = await resp.json().catch(()=>({}))
        if(resp.ok){
          setSuggestions(prev => ({ ...prev, [it.id]: j }))
        } else {
          skipped++
        }
      }catch(e){
        console.error('Bulk suggest failed for item', it.id, e)
        skipped++
      }
      done++
      setBulkProgress({ done, total: targets.length, skipped })
    }
    setBulkSuggesting(false)
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

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{width:900}} onClick={e=>e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>編集キュー — 未設定アイテム ({count}件)</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-search" style={{display:'flex', flexWrap:'wrap', alignItems:'center', gap:14}}>
          <span style={{fontSize:12, color:'#6b7280'}}>未設定とみなす項目:</span>
          {MISSING_FIELDS.map(f => (
            <label key={f.key} style={{display:'flex', alignItems:'center', gap:4, fontSize:12, cursor:'pointer'}}>
              <input type="checkbox" checked={activeFields.has(f.key)} onChange={()=>toggleField(f.key)} />
              {f.label}
            </label>
          ))}
        </div>

        <div className="cgm-panel-search" style={{display:'flex', alignItems:'center', gap:10}}>
          <button className="btn" onClick={runBulkSuggest} disabled={bulkSuggesting || items.every(it => !it.has_preview || suggestions[it.id])}>
            {bulkSuggesting
              ? `AI提案を実行中… (${bulkProgress ? bulkProgress.done : 0}/${bulkProgress ? bulkProgress.total : 0})`
              : '🏷 このキューを一括AI提案'}
          </button>
          <span style={{fontSize:11, color:'#6b7280'}}>
            {bulkSuggesting
              ? '1件あたり数秒〜かかります（Pi等では特に遅くなります）'
              : '各項目を開いた時点で提案が反映済みの状態になります'}
          </span>
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
                  #{it.id}{suggestions[it.id] && <span title="AI提案あり" style={{marginLeft:6}}>🏷</span>}
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
                    try{ window.dispatchEvent(new CustomEvent('item-updated', { detail: { id: selected.id, item: newItem } })) }catch(_){}
                    selectNext(selected.id)
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
