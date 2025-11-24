import React, { useState } from 'react'

function getCookie(name){
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

export default function EditFields({ item, onClose, onSaved }){
  const [characters, setCharacters] = useState((item.characters||[]).join(', '))
  const [tags, setTags] = useState((item.tags||[]).join(', '))
  const [titles, setTitles] = useState((item.titles||[]).join(', '))
  const [loading, setLoading] = useState(false)

  function parseList(str){
    if(str == null) return []
    return String(str).split(',').map(s=>s.trim()).filter(s=>s.length>0)
  }

  async function save(){
    const payload = {
      characters: parseList(characters),
      tags: tags.trim()===''? []: parseList(tags),
      titles: parseList(titles),
    }
    setLoading(true)
    try{
      const resp = await fetch(`/api/items/${item.id}/update_fields/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken')
        },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      })
      const j = await resp.json().catch(()=>({}))
      setLoading(false)
      if(!resp.ok){
        alert('Save failed: ' + (j.detail || JSON.stringify(j)))
        return
      }
      if(onSaved) onSaved(j.item)
      onClose()
    }catch(e){
      setLoading(false)
      console.error(e)
      alert('Save failed: ' + e.message)
    }
  }

  return (
    <div style={{position:'fixed', left:0, right:0, top:0, bottom:0, background:'rgba(0,0,0,0.5)', zIndex:1300}} onClick={onClose}>
      <div style={{width:520, maxWidth:'90%', margin:'6% auto', background:'#fff', padding:16}} onClick={e=>e.stopPropagation()}>
        <h3>Edit Item #{item.id}</h3>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Characters (comma separated)</label>
          <input style={{width:'100%'}} value={characters} onChange={e=>setCharacters(e.target.value)} />
        </div>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Tags (comma separated)</label>
          <input style={{width:'100%'}} value={tags} onChange={e=>setTags(e.target.value)} />
        </div>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Titles (comma separated)</label>
          <input style={{width:'100%'}} value={titles} onChange={e=>setTitles(e.target.value)} />
        </div>
        <div style={{marginTop:12}}>
          <button className="btn" onClick={save} disabled={loading}>{loading? 'Saving...' : 'Save'}</button>
          <button className="btn" style={{marginLeft:8}} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
