import React, { useState, useEffect, useRef } from 'react'
import { saveImagesChunked } from '../lib/saveImages'
import { fetchPreviewCandidates } from '../lib/fetchCandidates'
import { notify, postSync, onSync } from '../lib/crossWindowSync'

function timeAgo(ts){
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000))
  if(s < 60) return `${s}秒前`
  const m = Math.floor(s / 60)
  if(m < 60) return `${m}分前`
  const h = Math.floor(m / 60)
  if(h < 24) return `${h}時間前`
  return `${Math.floor(h / 24)}日前`
}

// Mailbox-style queue of fetched-but-not-yet-processed image candidates.
// Fetching an item's candidates (ScrollList's onFetch) enqueues an entry here
// instead of popping an inline modal — so an accidental click outside a modal
// backdrop can no longer discard results the user has to re-fetch. Entries
// stay here until explicitly saved or dismissed.
//
// `standalone`: rendered as a popped-out window instead of an overlay in the
// main window. The fetch queue's actual data lives in App.jsx's React state
// (it's ephemeral/in-memory only, unlike the edit queue which fetches its
// own data from the server) — a separate window has no access to that state
// via props, so in standalone mode this mirrors it over BroadcastChannel
// instead: request the current queue on mount, apply every subsequent sync,
// and route removals back through the main window (the source of truth)
// rather than mutating local state directly.
//
// The bulk-fetch button's TARGET LIST differs by mode for the same reason:
// non-standalone scopes to `currentPageItems` (the main window's current
// page — a concept standalone has no access to), while standalone instead
// queries /api/items/missing_preview/ directly (see that view's own
// docstring) so the button still has something to work with when opened as
// its own window with no page to inherit.
export default function FetchQueueManager({ queue: queueProp, onRemove: onRemoveProp, onClose, currentPageItems, onEnqueueFetch, standalone = false }){
  const [mirroredQueue, setMirroredQueue] = useState([])
  useEffect(() => {
    if(!standalone) return
    postSync('fetchQueue:request', null)
    return onSync('fetchQueue:sync', (payload) => setMirroredQueue(Array.isArray(payload) ? payload : []))
  }, [standalone])

  const queue = standalone ? mirroredQueue : queueProp

  // Standalone-only: this window's own view of "items with no preview yet",
  // loaded from the server instead of inherited via props. Same before_id
  // cursor pagination as EditQueueManager's `incomplete` load — see
  // ItemViewSet.missing_preview_queue's own docstring for why.
  const [missingItems, setMissingItems] = useState([])
  const [missingCount, setMissingCount] = useState(0)
  const [missingHasMore, setMissingHasMore] = useState(false)
  const [missingNextBeforeId, setMissingNextBeforeId] = useState(null)
  const [missingLoading, setMissingLoading] = useState(false)
  const [missingLoadingMore, setMissingLoadingMore] = useState(false)

  async function loadMissing(){
    if(!standalone) return
    setMissingLoading(true)
    try{
      const r = await fetch('/api/items/missing_preview/')
      const data = await r.json().catch(()=>({}))
      const list = data.results || []
      setMissingItems(list)
      setMissingCount(data.count ?? list.length)
      setMissingHasMore(!!data.has_more)
      setMissingNextBeforeId(data.next_before_id ?? null)
    }catch(e){
      console.error('Failed to load missing-preview items', e)
      setMissingItems([]); setMissingCount(0); setMissingHasMore(false); setMissingNextBeforeId(null)
    }finally{
      setMissingLoading(false)
    }
  }

  useEffect(() => { loadMissing() }, [standalone])

  async function loadMoreMissing(){
    if(!missingHasMore || missingNextBeforeId == null || missingLoadingMore) return
    setMissingLoadingMore(true)
    try{
      const r = await fetch(`/api/items/missing_preview/?before_id=${missingNextBeforeId}`)
      const data = await r.json().catch(()=>({}))
      const list = data.results || []
      setMissingItems(prev => [...prev, ...list])
      setMissingHasMore(!!data.has_more)
      setMissingNextBeforeId(data.next_before_id ?? null)
    }catch(e){
      console.error('Failed to load more missing-preview items', e)
    }finally{
      setMissingLoadingMore(false)
    }
  }

  function removeEntry(entryId){
    if(standalone){
      setMirroredQueue(prev => prev.filter(q => q.id !== entryId)) // optimistic; fetchQueue:sync reconciles shortly after
      postSync('fetchQueue:remove', entryId)
    } else {
      onRemoveProp(entryId)
    }
  }

  const [openId, setOpenId] = useState(queue.length > 0 ? queue[0].id : null)
  const [selectedUrls, setSelectedUrls] = useState(new Set())
  const [saving, setSaving] = useState(false)
  const [bulkRunning, setBulkRunning] = useState(false)
  const [bulkProgress, setBulkProgress] = useState(null) // {done, total}
  const [bulkSummary, setBulkSummary] = useState(null)

  // Kill switch for runBulkFetch — see EditQueueManager.jsx's identical
  // cancelledRef/abortRef pair for the full reasoning (applies here the
  // same way: closing this panel, overlay or standalone, must stop a
  // mid-flight bulk fetch from continuing to hit fetch_and_save_preview in
  // the background).
  const cancelledRef = useRef(false)
  const abortRef = useRef(null)
  useEffect(() => {
    abortRef.current = new AbortController()
    return () => { cancelledRef.current = true; abortRef.current.abort() }
  }, [])

  const openEntry = queue.find(q => q.id === openId) || null

  // Bulk button's target set: the current page's not-yet-fetched items in
  // overlay mode, or this window's own server-loaded list in standalone
  // mode (see missingItems above).
  const pendingItems = standalone
    ? missingItems
    : (currentPageItems || []).filter(it => it && !it.has_preview && it.link)

  function openEntryFor(entry){
    setOpenId(entry.id)
    setSelectedUrls(new Set())
  }

  async function runBulkFetch(){
    if(bulkRunning || pendingItems.length === 0) return
    setBulkRunning(true)
    setBulkSummary(null)
    let queued = 0, savedDirect = 0, failed = 0
    for(let i=0; i<pendingItems.length; i++){
      if(cancelledRef.current) return  // panel closed mid-run — stop immediately, no further state touches
      setBulkProgress({ done: i, total: pendingItems.length })
      const it = pendingItems[i]
      try{
        const res = await fetchPreviewCandidates(it.id, it.link, { signal: abortRef.current.signal })
        if(cancelledRef.current) return  // closed while this request was in flight — discard its result
        const body = res.body || {}
        if(res.ok && body.status === 'saved'){
          savedDirect++
          notify('item-preview-updated', { id: it.id })
        } else if(res.ok && body.preview_only && Array.isArray(body.images) && body.images.length > 0){
          onEnqueueFetch({ itemId: it.id, images: body.images })
          queued++
        } else {
          failed++
        }
      }catch(e){
        if(cancelledRef.current || (e && e.name === 'AbortError')) return
        console.error('Bulk fetch failed for item', it.id, e)
        failed++
      }
    }
    setBulkProgress({ done: pendingItems.length, total: pendingItems.length })
    setBulkRunning(false)
    setBulkSummary(`完了: キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件`)
    // Standalone's own list is a point-in-time server snapshot (unlike
    // overlay mode's currentPageItems, which the main window keeps live via
    // the 'item-preview-updated' listener) — reload it fresh so items just
    // saved directly drop off instead of being offered again next click.
    if(standalone) loadMissing()
  }

  async function save(entry, images){
    if(images.length === 0){ alert('画像を1枚以上選択してください'); return }
    setSaving(true)
    try{
      const { ok, body } = await saveImagesChunked(entry.itemId, images)
      setSaving(false)
      if(!ok){
        console.warn('save_previews failed', body)
        alert('保存に失敗しました: ' + (body && body.detail ? body.detail : JSON.stringify(body)))
        return
      }
      notify('item-preview-updated', { id: entry.itemId })
      removeEntry(entry.id)
      setSelectedUrls(new Set())
      const remaining = queue.filter(q => q.id !== entry.id)
      setOpenId(remaining.length > 0 ? remaining[0].id : null)
    }catch(e){
      setSaving(false)
      console.error(e)
      alert('保存に失敗しました: ' + (e && e.message ? e.message : String(e)))
    }
  }

  const content = (
    <>
      <div className="cgm-panel-header">
        <strong>取得キュー ({queue.length}件)</strong>
        <button className="cgm-panel-close" onClick={onClose}>{standalone ? 'ウィンドウを閉じる' : '✕'}</button>
      </div>

      <div className="cgm-panel-search" style={{display:'flex', alignItems:'center', gap:10, flexWrap:'wrap'}}>
        <button className="btn" onClick={runBulkFetch} disabled={bulkRunning || pendingItems.length===0 || (standalone && missingLoading)}>
          {bulkRunning
            ? `取得中… (${bulkProgress ? bulkProgress.done : 0}/${bulkProgress ? bulkProgress.total : pendingItems.length})`
            : standalone
              ? `未取得アイテムを一括取得 (${pendingItems.length}件${missingCount > pendingItems.length ? `/全${missingCount}件` : ''})`
              : `このページを一括取得 (${pendingItems.length}件)`}
        </button>
        {standalone && missingHasMore && (
          <button className="btn" onClick={loadMoreMissing} disabled={missingLoadingMore || bulkRunning}>
            {missingLoadingMore ? '読み込み中…' : 'もっと読み込む'}
          </button>
        )}
        {!bulkRunning && bulkSummary && <span style={{fontSize:12, color:'#6b7280'}}>{bulkSummary}</span>}
        {!bulkRunning && !bulkSummary && standalone && !missingLoading && pendingItems.length===0 && (
          <span style={{fontSize:12, color:'#6b7280'}}>未取得のアイテムはありません 🎉</span>
        )}
        {!bulkRunning && !bulkSummary && !standalone && pendingItems.length===0 && (currentPageItems || []).length>0 && (
          <span style={{fontSize:12, color:'#6b7280'}}>このページは全て取得済みです</span>
        )}
      </div>

        {queue.length === 0 ? (
          <div className="cgm-panel-body">
            <div className="cgm-empty-hint">キューは空です。画像を取得すると、ここに溜まっていきます。</div>
          </div>
        ) : (
          <div style={{display:'flex', minHeight:0, flex:'1 1 auto'}}>
            {/* Entry list (mailbox sidebar) */}
            <div style={{width:220, borderRight:'1px solid #f3f4f6', overflowY:'auto', flexShrink:0}}>
              {queue.map(entry => (
                <div
                  key={entry.id}
                  onClick={()=>openEntryFor(entry)}
                  style={{
                    padding:'10px 12px', cursor:'pointer',
                    background: entry.id===openId ? '#eff6ff' : 'transparent',
                    borderBottom:'1px solid #f3f4f6',
                  }}
                >
                  <div style={{fontSize:13, fontWeight:600}}>#{entry.itemId}</div>
                  <div style={{fontSize:12, color:'#6b7280'}}>{entry.images.length}件の候補 · {timeAgo(entry.fetchedAt)}</div>
                  <button
                    className="cgm-icon-btn cgm-icon-delete"
                    title="キューから削除"
                    onClick={e=>{ e.stopPropagation(); removeEntry(entry.id); if(entry.id===openId){ const rest = queue.filter(q=>q.id!==entry.id); setOpenId(rest.length>0?rest[0].id:null) } }}
                    style={{float:'right', marginTop:-2}}
                  >🗑</button>
                </div>
              ))}
            </div>

            {/* Selected entry's candidate grid */}
            <div style={{flex:1, padding:16, overflowY:'auto'}}>
              {!openEntry ? (
                <div className="cgm-empty-hint">左のリストから項目を選んでください</div>
              ) : (
                <div>
                  <div style={{marginBottom:12, fontSize:13, color:'#374151'}}>
                    アイテム #{openEntry.itemId} — {openEntry.images.length}件の候補({timeAgo(openEntry.fetchedAt)}取得)
                  </div>
                  <div style={{display:'flex', gap:12, flexWrap:'wrap', maxHeight:420, overflow:'auto'}}>
                    {openEntry.images.map((img, i) => (
                      <label key={i} style={{width:160, border: selectedUrls.has(img.url) ? '2px solid #3b82f6' : '2px solid #e5e7eb', borderRadius:6, padding:8, cursor:'pointer'}}>
                        <div style={{height:120, display:'flex', alignItems:'center', justifyContent:'center', background:'#f3f4f6', borderRadius:4}}>
                          {img.data_uri ? (
                            <img src={img.data_uri} alt={`cand-${i}`} style={{maxWidth:'100%', maxHeight:'100%', borderRadius:3}} />
                          ) : (
                            <div style={{fontSize:12, color:'#9ca3af'}}>No preview</div>
                          )}
                        </div>
                        <div style={{marginTop:6, display:'flex', alignItems:'center', gap:6}}>
                          <input type="checkbox" checked={selectedUrls.has(img.url)} onChange={e=>{
                            const s = new Set(selectedUrls)
                            if(e.target.checked) s.add(img.url)
                            else s.delete(img.url)
                            setSelectedUrls(s)
                          }} />
                          <span style={{fontSize:11, color:'#6b7280'}}>{img.size ? Math.round(img.size/1024)+'KB' : ''}</span>
                          <a href={img.url} target="_blank" rel="noreferrer" style={{fontSize:11, color:'#2563eb', marginLeft:'auto'}} onClick={e=>e.stopPropagation()}>↗</a>
                        </div>
                      </label>
                    ))}
                  </div>
                  <div style={{marginTop:16, display:'flex', gap:8, alignItems:'center'}}>
                    <button className="btn" style={{background:'#3b82f6', color:'#fff'}} disabled={saving}
                      onClick={()=>save(openEntry, openEntry.images.map(c=>({url:c.url, data_uri:c.data_uri})))}
                    >全選択して追加</button>
                    <button className="btn" disabled={saving || selectedUrls.size===0}
                      onClick={()=>save(openEntry, openEntry.images.filter(c=>selectedUrls.has(c.url)).map(c=>({url:c.url, data_uri:c.data_uri})))}
                    >選択した {selectedUrls.size} 件を追加</button>
                    <button className="btn" style={{marginLeft:'auto'}} onClick={()=>setSelectedUrls(new Set(openEntry.images.map(c=>c.url)))}>全選択</button>
                    <button className="btn" onClick={()=>setSelectedUrls(new Set())}>選択解除</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
    </>
  )

  if(standalone){
    return <div className="cgm-panel" style={{width:'100%', height:'100vh', maxHeight:'100vh', borderRadius:0}}>{content}</div>
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{width:820}} onClick={e=>e.stopPropagation()}>{content}</div>
    </div>
  )
}
