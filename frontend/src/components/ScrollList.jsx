import React, { useState, useRef, useEffect } from 'react'
import EditFields from './EditFields'
import { fetchPreviewCandidates } from '../lib/fetchCandidates'

function ItemRow({ it, readOnly, onEnqueueFetch }){
  const [url, setUrl] = useState(it.link || '')
  const [loading, setLoading] = useState(false)
  const [hasPreviewLocal, setHasPreviewLocal] = useState(!!it.has_preview)
  const [showTitles, setShowTitles] = useState(false)
  const [showTags, setShowTags] = useState(false)
  const [debugInfo, setDebugInfo] = useState(null)
  // debugInfo is kept for internal use; we do not render it in the UI.
  // Keep fetch debug objects available on `window.__fv_fetch_debug` and expose a helper to show them.
  const [fetchMethod, setFetchMethod] = useState('html')
  const [showEditor, setShowEditor] = useState(false)
  const [titlesState, setTitlesState] = useState(it.titles || [])
  const [charsState, setCharsState] = useState(it.characters || [])
  const [toast, setToast] = useState(null) // {msg, type:'loading'|'success'|'error'}
  const toastTimerRef = useRef(null)

  function showToast(msg, type = 'loading') {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    setToast({ msg, type })
    if (type !== 'loading') {
      toastTimerRef.current = setTimeout(() => setToast(null), 4500)
    }
  }
  const [tagsState, setTagsState] = useState(it.tags || [])
  const [situationState, setSituationState] = useState(it.situation || '')
  const [artistState, setArtistState] = useState(it.artist || '')
  const [copied, setCopied] = useState(false)

  // Saving now happens from the fetch queue (see FetchQueueManager), which is
  // a separate component from whichever ItemRow originally fetched the
  // candidates — so this row needs to hear about it after the fact to flip
  // its own preview thumbnail on.
  useEffect(()=>{
    function onPreviewUpdated(ev){
      if(ev && ev.detail && ev.detail.id === it.id) setHasPreviewLocal(true)
    }
    window.addEventListener('item-preview-updated', onPreviewUpdated)
    return () => window.removeEventListener('item-preview-updated', onPreviewUpdated)
  }, [it.id])

  async function onFetch(e){
    e && e.preventDefault()
    setLoading(true)
    // first fetch candidates (preview-only)
    const candRes = await fetchPreviewCandidates(it.id, url, { force_method: fetchMethod === 'api' ? 'api' : (fetchMethod === 'playwright' ? 'playwright' : undefined) })
    setLoading(false)
    if(!candRes.ok){
      const detail = candRes.body && candRes.body.detail
      // If HTML scraping found nothing and user didn't explicitly choose API,
      // offer to retry using API to give the user control over which method to use.
      if(detail && detail.toLowerCase().includes('no image candidates') && fetchMethod !== 'api'){
        const tryApi = window.confirm('HTML scraping found no image candidates. Try API-based fetch?')
        if(tryApi){
          setLoading(true)
          const apiRes = await fetchPreviewCandidates(it.id, url, {force_method: 'api'})
          setLoading(false)
          if(apiRes.ok){
            const body2 = apiRes.body || {}
            if(body2.status === 'saved'){
              setHasPreviewLocal(true)
              try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body2 }catch(e){}
              try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
              try{ window.alert('Preview saved via API.'); }catch(e){}
              return
            }
            if(body2.preview_only && Array.isArray(body2.images) && body2.images.length>0){
              onEnqueueFetch({ itemId: it.id, images: body2.images })
              try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body2 }catch(e){}
              showToast(`取得キューに追加しました(${body2.images.length}件) ✓`, 'success')
              return
            }
          }
        }
      }
      alert('Preview fetch failed. See console for details.')
      return
    }
    const body = candRes.body || {}
    // if server saved directly (no preview_only support), fallback to saved behavior
    if(body.status === 'saved'){
      setHasPreviewLocal(true)
      setDebugInfo(body)
      // store debug info globally for console inspection
      try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body }catch(e){}
      try{ if(!window.showFetchDebug) window.showFetchDebug = id => console.log(window.__fv_fetch_debug?.[id] || 'no debug for id '+id) }catch(e){}
      try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
      try{ window.alert('Preview saved.'); }catch(e){}
      return
    }

    // if preview_only returned images, queue them for later review instead of
    // popping a modal — see FetchQueueManager (opened from the app header).
    if(body.preview_only && Array.isArray(body.images) && body.images.length>0){
      onEnqueueFetch({ itemId: it.id, images: body.images })
      // save debug info globally for console inspection
      try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body }catch(e){}
      try{ if(!window.showFetchDebug) window.showFetchDebug = id => console.log(window.__fv_fetch_debug?.[id] || 'no debug for id '+id) }catch(e){}
      showToast(`取得キューに追加しました(${body.images.length}件) ✓`, 'success')
      return
    }

    alert('No preview candidates found.')
  }

  return (
    <div className="item" key={it.id}>
      <div className="item-id-badge">#{it.id}</div>
      <div className="meta-grid">
        <div className="col titles-col">
          <div className="col-header">Titles</div>
          <div className="chips">
            {Array.isArray(titlesState) && titlesState.length>0 ? (
              <>
                {titlesState.slice(0,2).map((t,i)=> (
                  <div key={i} style={{display:'inline-flex', alignItems:'center', marginRight:6}}>
                    <button className="chip" onClick={()=>{}} style={{paddingRight:8}}>{t}</button>
                      <button className="chip" onClick={async (e)=>{ e.stopPropagation(); try{ await navigator.clipboard.writeText(t); }catch(_){ window.prompt('Copy title:', t) } }} style={{marginLeft:4, padding:'6px'}} title="タイトルをコピー">
                        <img src="/icons/copy.svg" alt="Copy title" style={{width:16, height:16}} />
                      </button>
                    {it.link && <a className="chip" href={it.link} target="_blank" rel="noopener noreferrer" style={{marginLeft:4, padding:'6px'}} title="Open link"><img src="/icons/export-link.svg" alt="Open" style={{width:16, height:16}} /></a>}
                  </div>
                ))}
                {titlesState.length>2 && (
                  <button className="chip more" onClick={()=>setShowTitles(s=>!s)}>{showTitles? '▲' : `+${titlesState.length-2}`}</button>
                )}
              </>
            ) : (
              <div className="empty">—</div>
            )}
            {showTitles && Array.isArray(titlesState) && (
              <div className="chip-dropdown">
                {titlesState.map((t,i)=> (
                  <div key={i} className="chip-row" style={{display:'flex', alignItems:'center', justifyContent:'space-between'}}>
                    <div style={{flex:1, wordBreak:'break-word'}}>{t}</div>
                    <div style={{marginLeft:8, display:'flex', alignItems:'center', gap:6}}>
                        <button className="chip" onClick={async ()=>{ try{ await navigator.clipboard.writeText(t); }catch(_){ window.prompt('Copy title:', t) } }} title="タイトルをコピー" style={{padding:6}}>
                        <img src="/icons/copy.svg" alt="Copy" style={{width:16, height:16}} />
                      </button>
                      {it.link && <a className="chip" href={it.link} target="_blank" rel="noopener noreferrer" title="Open link" style={{padding:6}}><img src="/icons/export-link.svg" alt="Open" style={{width:16, height:16}} /></a>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {/* Situation chip */}
            <div style={{marginLeft:8}}>
              {situationState && (
                <span className={`situation-chip s-${(situationState||'').toLowerCase()}`}>{(situationState||'').toUpperCase()}</span>
              )}
            </div>
            {/* Characters: always show all characters when present */}
            {Array.isArray(charsState) && charsState.length>0 && (
              <div className="chips" style={{marginTop:8}}>
                {charsState.map((c,i)=> <button key={i} className="chip" onClick={()=>{}}>{c}</button>)}
              </div>
            )}
          </div>
        </div>

        <div className="col artist-col">
          <div className="col-header">Artist</div>
          <div className="artist-chip">{artistState || '—'}</div>
        </div>

        <div className="col tags-col">
          <div className="col-header">Tags</div>
          <div className="chips">
            {Array.isArray(tagsState) && tagsState.length>0 ? (
              <>
                {tagsState.slice(0,3).map((tag,i)=> <button key={i} className="chip" onClick={()=>{}}>{tag}</button>)}
                {tagsState.length>3 && (
                  <button className="chip more" onClick={()=>setShowTags(s=>!s)}>{showTags? '▲' : `+${tagsState.length-3}`}</button>
                )}
              </>
            ) : (
              <div className="empty">—</div>
            )}
            {showTags && Array.isArray(tagsState) && (
              <div className="chip-dropdown">
                {tagsState.map((tag,i)=> <div key={i} className="chip-row">{tag}</div>)}
              </div>
            )}
          </div>
        </div>

        <div className="col preview-col">
          <div className="col-header">Preview</div>
          <div className="preview-wrap">
            {hasPreviewLocal ? (
              <img className="preview" src={`/api/items/${it.id}/preview/`} alt="preview" />
            ) : (
              // show link only in preview area; actions are provided next to titles
              <div style={{overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}} title={it.link}>
                <a href={it.link} target="_blank" rel="noopener noreferrer" className="link-text">{it.link}</a>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="actions-row">
        {!readOnly && (
          <div style={{display:'inline-block'}}>
            <input className="url-input" type="text" value={url} onChange={e=>setUrl(e.target.value)}
              onKeyDown={e=>{ if(e.key==='Enter'){ e.preventDefault(); onFetch(e) } }} />
            <select value={fetchMethod} onChange={e=>setFetchMethod(e.target.value)} style={{marginLeft:8, marginRight:8}} title="Choose fetch method">
              <option value="html">HTML scrape</option>
              <option value="api">Use API</option>
              <option value="playwright">Use Browser (Playwright)</option>
            </select>
            <button className="btn" type="button" onClick={onFetch} disabled={loading} style={{padding:'7px 12px', lineHeight:1, fontSize:24}} title="候補を取得してプレビューに追加">
              {loading ? '…' : '+'}
            </button>
          </div>
        )}
        {!readOnly && <button className="btn" style={{marginLeft:8, padding:'7px 10px', lineHeight:1}} title="Clear previews" onClick={async ()=>{
          const ok = window.confirm('Clear all previews for this item? This cannot be undone.')
          if(!ok) return
          try{
            const resp = await fetch(`/api/items/${it.id}/previews/`, {method:'DELETE'})
            if(!resp.ok){ const j = await resp.json().catch(()=>({})); alert('Failed to clear previews: '+(j.detail||j.error||resp.status)); return }
            setHasPreviewLocal(false)
            try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
            alert('Previews cleared.')
          }catch(e){ console.error(e); alert('Failed to clear previews') }
        }}>
          <svg width="24" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M15 4l-3 3-2.5-.5L3 13l3 3 1-1 1 1 3-3-.5-2.5 3-3z"/><line x1="6" y1="16" x2="4" y2="18"/><line x1="8" y1="18" x2="6" y2="20"/><line x1="10" y1="17" x2="8" y2="19"/></svg>
        </button>}
        {!readOnly && <>
          <button className="btn" style={{marginLeft:8, background:'#a33', color:'#fff', padding:'7px 10px', lineHeight:1}} title="Delete item" onClick={async ()=>{
            const ok = window.confirm('Delete this item from the database? This will remove its previews too.')
            if(!ok) return
            try{
              const resp = await fetch(`/api/items/${it.id}/delete_item/`, {method:'DELETE'})
              if(!resp.ok){ const j = await resp.json().catch(()=>({})); alert('Failed to delete item: '+(j.detail||j.error||resp.status)); return }
              try{ window.dispatchEvent(new CustomEvent('item-deleted', { detail: { id: it.id } })) }catch(e){}
              alert('Item deleted.')
            }catch(e){ console.error(e); alert('Failed to delete item') }
          }}>
            <svg width="24" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}>
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/>
            </svg>
          </button>
          <button className="btn" style={{marginLeft:8, padding:'7px 10px', lineHeight:1}} title="Edit fields" onClick={()=>setShowEditor(true)}>
            <svg width="24" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
        </>}
        {/* fetch debug is stored internally; use `window.showFetchDebug(id)` in the console to inspect */}
      </div>
      

      {/* EditFields component is hidden by default. To restore inline editing,
          uncomment the block below. Keep it commented to avoid showing edit UI on the page. */}
      {
      showEditor && (
        <EditFields item={it} onClose={()=>setShowEditor(false)} onSaved={(newItem)=>{
          // update local chars/tags state
          setTitlesState(newItem.titles || [])
          setCharsState(newItem.characters || [])
          setTagsState(newItem.tags || [])
          setSituationState(newItem.situation || '')
          setArtistState(newItem.artist || '')
          setShowEditor(false)
          // Carry the full updated item (update_fields returns it fully
          // serialized) so App.jsx can merge it into the shared items list —
          // otherwise reopening the editor later reads the stale pre-edit
          // object and the user has to re-enter everything from scratch.
          try{ window.dispatchEvent(new CustomEvent('item-updated', { detail: { id: it.id, item: newItem } })) }catch(e){}
        }} />
      )}

      {toast && (
        <div className={`fv-toast${toast.type === 'success' ? ' fv-toast--success' : toast.type === 'error' ? ' fv-toast--error' : ''}`}>
          {toast.type === 'loading' && <div className="fv-toast__spinner" />}
          {toast.type === 'success' && <span className="fv-toast__icon">✓</span>}
          {toast.type === 'error'   && <span className="fv-toast__icon">⚠</span>}
          <span className="fv-toast__msg">{toast.msg}</span>
          <button className="fv-toast__close" onClick={() => setToast(null)}>✕</button>
        </div>
      )}
    </div>
  )
}

export default function ScrollList({items, readOnly=false, onEnqueueFetch}){
  return (
    <div className="scroll-list">
      {items.map(it=> (
        <ItemRow it={it} key={it.id} readOnly={readOnly} onEnqueueFetch={onEnqueueFetch} />
      ))}
    </div>
  )
}
