import React, {useState} from 'react'
import apiFetch, {apiUrl} from '../api'

export default function RestorePreviews(){
  const [file, setFile] = useState(null)
  const [password, setPassword] = useState('')
  const [dryRun, setDryRun] = useState(true)
  const [status, setStatus] = useState(null)
  const [running, setRunning] = useState(false)

  async function submit(e){
    e.preventDefault()
    if(!file){
      setStatus({error: 'No file selected'})
      return
    }
    setRunning(true)
    setStatus({info: 'Uploading...'})
    try{
      const fd = new FormData()
      fd.append('file', file)
      fd.append('password', password)
      if(dryRun) fd.append('dry_run', '1')

      const url = apiUrl('/api/admin/restore_previews/')
      const r = await fetch(url, { method: 'POST', body: fd, mode: 'cors' })
      const text = await r.text()
      let body = null
      try{ body = JSON.parse(text) }catch(_){ body = {raw: text} }
      if(!r.ok){
        setStatus({error: `Server returned ${r.status}`, body})
      } else {
        setStatus({ok: true, body})
      }
    }catch(err){
      setStatus({error: err.message || String(err)})
    }finally{
      setRunning(false)
    }
  }

  return (
    <form onSubmit={submit} className="restore-previews-form" style={{display:'inline-block', marginLeft:12}}>
      <label style={{fontSize:12, color:'#444', marginRight:8}}>Restore previews</label>
      <input type="file" accept="application/json" onChange={e=>setFile(e.target.files && e.target.files[0])} style={{marginRight:8}} />
      <input type="password" placeholder="password" value={password} onChange={e=>setPassword(e.target.value)} style={{marginRight:8}} />
      <label style={{marginRight:8, fontSize:12}}>
        <input type="checkbox" checked={dryRun} onChange={e=>setDryRun(e.target.checked)} /> dry-run
      </label>
      <button className="btn" type="submit" disabled={running}>{running? 'Running…' : 'Run'}</button>
      {status && (
        <div style={{marginTop:8, maxWidth:600, whiteSpace:'pre-wrap', fontFamily:'monospace', fontSize:12}}>
          {status.error && <div style={{color:'crimson'}}>Error: {String(status.error)}</div>}
          {status.info && <div>{status.info}</div>}
          {status.ok && status.body && <div style={{color:'green'}}>Success:\n{JSON.stringify(status.body, null, 2)}</div>}
          {status.body && !status.ok && <div>Response:\n{JSON.stringify(status.body, null, 2)}</div>}
        </div>
      )}
    </form>
  )
}
