import React, { useState, useEffect } from 'react'
import CharacterPicker from './CharacterPicker'

function getCookie(name){
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

const SECTION = {
  label: { color:'#94a3b8', fontSize:11, fontWeight:600, letterSpacing:'0.08em', textTransform:'uppercase', marginBottom:6, display:'block' },
  wrap:  { marginBottom:16, background:'#0f172a', borderRadius:8, padding:'12px 14px' },
}

const chipStyle = {
  background:'#1e3a5f', color:'#93c5fd', borderRadius:4,
  padding:'5px 10px', fontSize:13, display:'inline-flex', alignItems:'center', gap:6,
}

function TagField({ label, hint, list, setList, allOptions, setAllOptions, selectPlaceholder }){
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)

  const available = allOptions.filter(t=>!list.includes(t))
  const q = query.trim().toLowerCase()
  const filtered = q ? available.filter(t=>t.toLowerCase().includes(q)) : available
  const exactExists = q && [...available, ...list].some(t=>t.toLowerCase()===q)

  function addExisting(t){
    if(!list.includes(t)) setList(prev=>[...prev, t])
    setQuery('')
  }

  function addNew(){
    const t = query.trim()
    if(!t) return
    if(!list.includes(t)) setList(prev=>[...prev, t])
    if(!allOptions.includes(t)) setAllOptions(prev=>[...prev, t].sort())
    setQuery('')
  }

  return (
    <div style={SECTION.wrap}>
      <label style={SECTION.label}>{label}</label>
      {hint && <div style={{fontSize:12, color:'#64748b', marginBottom:8}}>{hint}</div>}
      <div style={{display:'flex', flexWrap:'wrap', gap:6, marginBottom:8, minHeight:28}}>
        {list.length === 0
          ? <span style={{fontSize:13, color:'#475569'}}>未選択</span>
          : list.map(t=>(
            <span key={t} style={chipStyle}>
              {t}
              <button onClick={()=>setList(prev=>prev.filter(x=>x!==t))}
                style={{border:'none', background:'none', cursor:'pointer', padding:0, lineHeight:1, fontSize:16, color:'#93c5fd'}}>×</button>
            </span>
          ))}
      </div>
      <div style={{position:'relative'}}>
        <input
          style={{width:'100%', background:'#1e293b', color:'#f1f5f9', border:'1px solid #334155',
            borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
          placeholder={selectPlaceholder}
          value={query}
          onChange={e=>setQuery(e.target.value)}
          onFocus={()=>setFocused(true)}
          onBlur={()=>setTimeout(()=>setFocused(false), 150)}
          onKeyDown={e=>{
            if(e.key!=='Enter') return
            if(filtered.length===1) addExisting(filtered[0])
            else if(q && !exactExists) addNew()
          }}
        />
        {focused && (
          <div style={{position:'absolute', top:'100%', left:0, right:0, marginTop:4, background:'#1e293b',
            border:'1px solid #334155', borderRadius:6, maxHeight:180, overflowY:'auto', zIndex:10,
            boxShadow:'0 8px 24px rgba(0,0,0,0.4)'}}>
            {filtered.slice(0, 30).map(t=>(
              <button key={t} onMouseDown={e=>e.preventDefault()} onClick={()=>addExisting(t)}
                style={{display:'block', width:'100%', textAlign:'left', background:'none', border:'none',
                  color:'#f1f5f9', padding:'8px 12px', fontSize:14, cursor:'pointer'}}
                onMouseEnter={e=>e.currentTarget.style.background='#334155'}
                onMouseLeave={e=>e.currentTarget.style.background='none'}
              >{t}</button>
            ))}
            {q && !exactExists && (
              <button onMouseDown={e=>e.preventDefault()} onClick={addNew}
                style={{display:'block', width:'100%', textAlign:'left', background:'none', border:'none',
                  borderTop: filtered.length>0 ? '1px solid #334155' : 'none',
                  color:'#93c5fd', padding:'8px 12px', fontSize:14, cursor:'pointer'}}
              >＋ 「{query.trim()}」を新規作成</button>
            )}
            {filtered.length===0 && !q && (
              <div style={{padding:'8px 12px', fontSize:13, color:'#64748b'}}>候補なし</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function EditFields({ item, onClose, onSaved }){
  const [titleList, setTitleList] = useState(item.titles||[])
  const [charList,  setCharList]  = useState(item.characters||[])
  const [situation, setSituation] = useState((item.situation||'').toUpperCase())
  const [tags,      setTags]      = useState((item.tags||[]).join(', '))
  const [artist,    setArtist]    = useState(item.artist||'')
  const [loading,   setLoading]   = useState(false)
  const [allTitles, setAllTitles] = useState([])
  const [allChars,  setAllChars]  = useState([])

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
      situation,
      tags: tags.trim()===''? [] : parseList(tags),
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
      alert('Save failed: ' + e.message)
    }
  }

  const SITUATIONS = [
    { value:'',        label:'— 未設定 —' },
    { value:'SOLO',    label:'SOLO' },
    { value:'CP',      label:'CP' },
    { value:'MULTIPLE',label:'MULTIPLE' },
    { value:'PARODY',  label:'PARODY' },
    { value:'R18',     label:'R18' },
  ]

  return (
    <div style={{position:'fixed', left:0, right:0, top:0, bottom:0, background:'rgba(0,0,0,0.65)', zIndex:1300}} onClick={onClose}>
      <div
        style={{width:540, maxWidth:'92%', margin:'3% auto', background:'#1e293b',
          borderRadius:12, padding:'20px 24px', maxHeight:'92vh', overflowY:'auto',
          boxShadow:'0 12px 48px rgba(0,0,0,0.7)'}}
        onClick={e=>e.stopPropagation()}
      >
        <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:20}}>
          <span style={{color:'#f8fafc', fontWeight:700, fontSize:16}}>Edit #{item.id}</span>
          <button className="btn" style={{padding:'4px 10px'}} onClick={onClose}>✕</button>
        </div>

        <TagField
          label="Titles ★"
          hint="このイラストの作品名・シリーズ名を選択してください"
          list={titleList} setList={setTitleList}
          allOptions={allTitles} setAllOptions={setAllTitles}
          selectPlaceholder="タイトルを検索、または新規入力（必須）"
        />

        <div style={SECTION.wrap}>
          <label style={SECTION.label}>Characters</label>
          <CharacterPicker charList={charList} setCharList={setCharList} allChars={allChars} titles={titleList} />
        </div>

        <div style={SECTION.wrap}>
          <label style={SECTION.label}>Artist</label>
          <input
            style={{width:'100%', background:'#0f172a', color:'#f1f5f9', border:'1px solid #334155',
              borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
            value={artist} onChange={e=>setArtist(e.target.value)}
            placeholder="作者名 / Twitter ID"
          />
        </div>

        <div style={SECTION.wrap}>
          <label style={SECTION.label}>Situation</label>
          <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
            {SITUATIONS.map(s=>(
              <button key={s.value} onClick={()=>setSituation(s.value)}
                style={{
                  padding:'8px 16px', borderRadius:6, border:'none', cursor:'pointer', fontSize:13, fontWeight:500,
                  background: situation===s.value ? '#3b82f6' : '#0f172a',
                  color: situation===s.value ? '#fff' : '#94a3b8',
                  outline: situation===s.value ? '2px solid #3b82f6' : '1px solid #334155',
                }}
              >{s.label || '—'}</button>
            ))}
          </div>
        </div>

        <div style={SECTION.wrap}>
          <label style={SECTION.label}>Tags <span style={{fontWeight:400, textTransform:'none', fontSize:11}}>(カンマ区切り)</span></label>
          <input
            style={{width:'100%', background:'#0f172a', color:'#f1f5f9', border:'1px solid #334155',
              borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
            value={tags} onChange={e=>setTags(e.target.value)}
            placeholder="tag1, tag2, tag3"
          />
        </div>

        <div style={{display:'flex', gap:8, marginTop:4}}>
          <button className="btn" style={{background:'#3b82f6', color:'#fff', padding:'10px 24px', fontSize:14, fontWeight:600}}
            onClick={save} disabled={loading}>
            {loading ? '保存中…' : '保存'}
          </button>
          <button className="btn" style={{padding:'10px 16px'}} onClick={onClose}>キャンセル</button>
        </div>
      </div>
    </div>
  )
}
