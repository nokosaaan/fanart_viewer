import React, { useState, useEffect } from 'react'
import CharacterPicker from './CharacterPicker'

function getCookie(name){
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

const chipStyle = {background:'#e0e0e0', borderRadius:3, padding:'2px 6px', fontSize:13, display:'flex', alignItems:'center', gap:4}

function TagField({ label, list, setList, allOptions, setAllOptions, selectPlaceholder, newInputPlaceholder }){
  const [showNew, setShowNew] = useState(false)
  const [newInput, setNewInput] = useState('')

  function handleSelect(e){
    const val = e.target.value
    if(!val) return
    if(val === '__new__'){
      setShowNew(true)
      e.target.value = ''
      return
    }
    if(!list.includes(val)) setList(prev=>[...prev, val])
    e.target.value = ''
  }

  function addNew(){
    const t = newInput.trim()
    if(!t) return
    if(!list.includes(t)) setList(prev=>[...prev, t])
    if(!allOptions.includes(t)) setAllOptions(prev=>[...prev, t].sort())
    setNewInput('')
    setShowNew(false)
  }

  const available = allOptions.filter(t=>!list.includes(t))

  return (
    <div style={{marginBottom:8}}>
      <label style={{display:'block', fontSize:12}}>{label}</label>
      <div style={{display:'flex', flexWrap:'wrap', gap:4, marginBottom:4, minHeight:24}}>
        {list.map(t=>(
          <span key={t} style={chipStyle}>
            {t}
            <button onClick={()=>setList(prev=>prev.filter(x=>x!==t))} style={{border:'none', background:'none', cursor:'pointer', padding:0, lineHeight:1, fontSize:14}}>×</button>
          </span>
        ))}
      </div>
      <select style={{width:'100%'}} onChange={handleSelect} defaultValue="">
        <option value="" disabled>{selectPlaceholder}</option>
        {available.map(t=><option key={t} value={t}>{t}</option>)}
        <option value="__new__">＋ 新規作成...</option>
      </select>
      {showNew && (
        <div style={{display:'flex', gap:4, marginTop:4}}>
          <input
            style={{flex:1}}
            placeholder={newInputPlaceholder}
            value={newInput}
            onChange={e=>setNewInput(e.target.value)}
            onKeyDown={e=>{ if(e.key==='Enter') addNew() }}
            autoFocus
          />
          <button className="btn" onClick={addNew}>追加</button>
          <button className="btn" onClick={()=>{ setShowNew(false); setNewInput('') }}>キャンセル</button>
        </div>
      )}
    </div>
  )
}

export default function EditFields({ item, onClose, onSaved }){
  const [titleList, setTitleList] = useState(item.titles||[])
  const [charList, setCharList] = useState(item.characters||[])
  const [situation, setSituation] = useState((item.situation||'').toUpperCase())
  const [tags, setTags] = useState((item.tags||[]).join(', '))
  const [artist, setArtist] = useState(item.artist||'')
  const [loading, setLoading] = useState(false)
  const [allTitles, setAllTitles] = useState([])
  const [allChars, setAllChars] = useState([])

  useEffect(()=>{
    fetch('/api/items/all_titles/')
      .then(r=>r.json()).then(d=>{ if(Array.isArray(d)) setAllTitles(d) }).catch(()=>{})
    fetch('/api/items/all_characters/')
      .then(r=>r.json()).then(d=>{ if(Array.isArray(d)) setAllChars(d) }).catch(()=>{})
  }, [])

  function parseList(str){
    if(str == null) return []
    return String(str).split(',').map(s=>s.trim()).filter(s=>s.length>0)
  }

  async function save(){
    const payload = {
      titles: titleList,
      characters: charList,
      situation: situation,
      tags: tags.trim()===''? []: parseList(tags),
      artist: artist.trim(),
    }
    setLoading(true)
    try{
      const resp = await fetch(`/api/items/${item.id}/update_fields/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      })
      const j = await resp.json().catch(()=>({}))
      setLoading(false)
      if(!resp.ok){ alert('Save failed: ' + (j.detail || JSON.stringify(j))); return }
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
      <div style={{width:520, maxWidth:'90%', margin:'4% auto', background:'#fff', padding:16, maxHeight:'90vh', overflowY:'auto'}} onClick={e=>e.stopPropagation()}>
        <h3>Edit Item #{item.id}</h3>
        <TagField
          label="Titles"
          list={titleList} setList={setTitleList}
          allOptions={allTitles} setAllOptions={setAllTitles}
          selectPlaceholder="— タイトルを選択 —"
          newInputPlaceholder="新しいタイトルを入力"
        />
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12, marginBottom:4}}>Characters</label>
          <CharacterPicker charList={charList} setCharList={setCharList} allChars={allChars} />
        </div>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Artist</label>
          <input style={{width:'100%'}} value={artist} onChange={e=>setArtist(e.target.value)} placeholder="artist name / Twitter ID" />
        </div>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Situation</label>
          <select style={{width:'100%'}} value={situation} onChange={e=>setSituation(e.target.value)}>
            <option value="">—</option>
            <option value="SOLO">SOLO</option>
            <option value="CP">CP</option>
            <option value="MULTIPLE">MULTIPLE</option>
            <option value="PARODY">PARODY</option>
            <option value="R18">R18</option>
          </select>
        </div>
        <div style={{marginBottom:8}}>
          <label style={{display:'block', fontSize:12}}>Tags (comma separated)</label>
          <input style={{width:'100%'}} value={tags} onChange={e=>setTags(e.target.value)} />
        </div>
        <div style={{marginTop:12}}>
          <button className="btn" onClick={save} disabled={loading}>{loading? 'Saving...' : 'Save'}</button>
          <button className="btn" style={{marginLeft:8}} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
