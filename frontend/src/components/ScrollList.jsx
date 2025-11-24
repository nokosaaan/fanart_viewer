import React, { useState } from 'react'
import EditFields from './EditFields'

async function fetchAndSavePreview(id, url, options = {}){
  try{
    const opts = {
      method: 'POST',
      headers: {}
    }
    if(url){
      opts.headers['Content-Type'] = 'application/json'
      const body = { url }
      if(options.force_method) body.force_method = options.force_method
      opts.body = JSON.stringify(body)
    }
    const resp = await fetch(`/api/items/${id}/fetch_and_save_preview/`, opts)
    if(!resp.ok) {
      const j = await resp.json().catch(()=>({}));
      console.warn('Preview fetch failed', j)
      return { ok: false, body: j }
    }
    return { ok: true, body: await resp.json().catch(()=>({})) }
  }catch(e){ console.error(e); return { ok: false, body: {error: e.message} } }
}

async function fetchPreviewCandidates(id, url, options = {}){
  try{
    const body = {}
    if(url) body.url = url
    body.preview_only = true
    // only include force_method when explicitly requested by the UI
    if(options.force_method) body.force_method = options.force_method
    const resp = await fetch(`/api/items/${id}/fetch_and_save_preview/`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)})
    if(!resp.ok) {
      const j = await resp.json().catch(()=>({}));
      return { ok: false, body: j }
    }
    return { ok: true, body: await resp.json().catch(()=>({})) }
  }catch(e){ console.error(e); return { ok: false, body: {error: e.message} } }
}

function ItemRow({ it }){
  const [url, setUrl] = useState(it.link || '')
  const [loading, setLoading] = useState(false)
  const [hasPreviewLocal, setHasPreviewLocal] = useState(!!it.has_preview)
  const [showTitles, setShowTitles] = useState(false)
  const [showTags, setShowTags] = useState(false)
  const [debugInfo, setDebugInfo] = useState(null)
  // debugInfo is kept for internal use; we do not render it in the UI.
  // Keep fetch debug objects available on `window.__fv_fetch_debug` and expose a helper to show them.
  const [candidates, setCandidates] = useState(null)
  const [showCandidates, setShowCandidates] = useState(false)
  const [fetchMethod, setFetchMethod] = useState('html')
  const [selectedUrls, setSelectedUrls] = useState(new Set())
  const [showEditor, setShowEditor] = useState(false)
  const [charsState, setCharsState] = useState(it.characters || [])
  const [tagsState, setTagsState] = useState(it.tags || [])

  async function onFetch(e){
    e && e.preventDefault()
    // confirm that this action will use external APIs / network resources
    const ok = window.confirm('This will fetch the provided URL and may consume external APIs or network resources. Continue?')
    if(!ok) return
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
              setCandidates(body2.images)
              setSelectedUrls(new Set())
              setShowCandidates(true)
              try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body2 }catch(e){}
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

    // if preview_only returned images, show selection UI
    if(body.preview_only && Array.isArray(body.images) && body.images.length>0){
      setCandidates(body.images)
      setSelectedUrls(new Set())
      setShowCandidates(true)
      // save debug info globally for console inspection
      try{ window.__fv_fetch_debug = window.__fv_fetch_debug || {}; window.__fv_fetch_debug[it.id] = body }catch(e){}
      try{ if(!window.showFetchDebug) window.showFetchDebug = id => console.log(window.__fv_fetch_debug?.[id] || 'no debug for id '+id) }catch(e){}
      return
    }

    alert('No preview candidates found.')
  }

  async function saveSelected(){
    if(!candidates) return
    const urls = Array.from(selectedUrls)
    if(urls.length===0){ alert('Select at least one image to save'); return }
    setLoading(true)
    try{
      // build images payload including data_uri when available to ensure saving
      const images = candidates.filter(c=> urls.includes(c.url)).map(c=> ({url: c.url, data_uri: c.data_uri}))
      const resp = await fetch(`/api/items/${it.id}/save_previews/`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({images})})
      const j = await resp.json().catch(()=>({}))
      setLoading(false)
      if(resp.ok){
        setHasPreviewLocal(true)
        setShowCandidates(false)
        try{ window.dispatchEvent(new CustomEvent('item-preview-updated', { detail: { id: it.id } })) }catch(e){}
        try{ window.alert('Selected previews saved.'); }catch(e){}
      } else {
        console.warn('save_previews failed', j)
        alert('Save failed. See console.')
      }
    }catch(e){ setLoading(false); console.error(e); alert('Save failed') }
  }

  return (
    <div className="item" key={it.id}>
      <div className="meta-grid">
        <div className="col titles-col">
          <div className="col-header">Titles</div>
          <div className="chips">
            {Array.isArray(it.titles) && it.titles.length>0 ? (
              <>
                {it.titles.slice(0,2).map((t,i)=> <button key={i} className="chip" onClick={()=>{}}>{t}</button>)}
                {it.titles.length>2 && (
                  <button className="chip more" onClick={()=>setShowTitles(s=>!s)}>{showTitles? '▲' : `+${it.titles.length-2}`}</button>
                )}
              </>
            ) : (
              <div className="empty">—</div>
            )}
            {showTitles && Array.isArray(it.titles) && (
              <div className="chip-dropdown">
                {it.titles.map((t,i)=> <div key={i} className="chip-row">{t}</div>)}
              </div>
            )}
            {/* Situation chip */}
            <div style={{marginLeft:8}}>
              {it.situation && (
                <span className={`situation-chip s-${(it.situation||'').toLowerCase()}`}>{(it.situation||'').toUpperCase()}</span>
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
          <div className="artist-chip">{it.artist || '—'}</div>
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
              <a href={it.link} target="_blank" rel="noopener noreferrer" className="link-text">{it.link}</a>
            )}
          </div>
        </div>
      </div>

      <div className="actions-row">
        <form onSubmit={onFetch} style={{display:'inline-block'}}>
          <input className="url-input" type="text" value={url} onChange={e=>setUrl(e.target.value)} />
          <select value={fetchMethod} onChange={e=>setFetchMethod(e.target.value)} style={{marginLeft:8, marginRight:8}} title="Choose fetch method">
            <option value="html">HTML scrape</option>
            <option value="api">Use API</option>
            <option value="playwright">Use Browser (Playwright)</option>
          </select>
          <button className="btn" type="submit" disabled={loading}>{loading? 'Fetching...' : 'Fetch Preview'}</button>
        </form>
        {// Edit fields UI is hidden by default. To re-enable editing, uncomment the button below:
        <button className="btn" style={{marginLeft:8}} onClick={()=>setShowEditor(true)}>Edit fields</button>
        }
        {/* fetch debug is stored internally; use `window.showFetchDebug(id)` in the console to inspect */}
      </div>
      

      {/* Candidate selection modal */}
      {showCandidates && candidates && (
        <div style={{position:'fixed', left:0, right:0, top:0, bottom:0, background:'rgba(0,0,0,0.5)', zIndex:1200}} onClick={()=>setShowCandidates(false)}>
          <div style={{width:'80%', maxWidth:900, margin:'5% auto', background:'#fff', padding:16}} onClick={e=>e.stopPropagation()}>
            <h3>Select images to save</h3>
            <div style={{display:'flex', gap:12, flexWrap:'wrap', maxHeight:400, overflow:'auto'}}>
              {candidates.map((img, i)=> (
                <label key={i} style={{width:160, border:'1px solid #ddd', padding:8}}>
                  <div style={{height:120, display:'flex', alignItems:'center', justifyContent:'center', background:'#f6f6f6'}}>
                    {img.data_uri ? (
                      <img src={img.data_uri} alt={`cand-${i}`} style={{maxWidth:'100%', maxHeight:'100%'}} />
                    ) : (
                      <div style={{fontSize:12, color:'#666'}}>No preview</div>
                    )}
                  </div>
                  <div style={{marginTop:6}}>
                    <input type="checkbox" onChange={e=>{
                      const s = new Set(selectedUrls)
                      if(e.target.checked) s.add(img.url)
                      else s.delete(img.url)
                      setSelectedUrls(s)
                    }} /> <span style={{fontSize:12}}>{img.size} bytes</span>
                  </div>
                  <div style={{fontSize:11, wordBreak:'break-all', marginTop:6}}><a href={img.url} target="_blank" rel="noreferrer">open</a></div>
                </label>
              ))}
            </div>
            <div style={{marginTop:12}}>
              <button className="btn" onClick={saveSelected} disabled={loading}>Save selected</button>
              <button className="btn" style={{marginLeft:8}} onClick={()=>setShowCandidates(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* EditFields component is hidden by default. To restore inline editing,
          uncomment the block below. Keep it commented to avoid showing edit UI on the page. */}
      {
      showEditor && (
        <EditFields item={it} onClose={()=>setShowEditor(false)} onSaved={(newItem)=>{
          // update local chars/tags state
          setCharsState(newItem.characters || [])
          setTagsState(newItem.tags || [])
          setShowEditor(false)
          try{ window.dispatchEvent(new CustomEvent('item-updated', { detail: { id: it.id } })) }catch(e){}
        }} />
      )}
    </div>
  )
}

export default function ScrollList({items}){
  return (
    <div className="scroll-list">
      {items.map(it=> (
        <ItemRow it={it} key={it.id} />
      ))}
    </div>
  )
}
