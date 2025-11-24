import React, {useState} from 'react'

export default function SearchBar({query, setQuery, suggestions, onAddSuggestion, filters, onRemoveFilter, includeCP, setIncludeCP, previewOpen, setPreviewOpen, situationFilter, setSituationFilter}){
  const [open, setOpen] = useState(false)

  function onToggleCP(e){
    const willEnable = e.target.checked
    if(willEnable){
      const ok = window.confirm('閲覧注意: CP（カップリング）を含む可能性のあるコンテンツが表示されます。表示を続けてもよいですか？')
      if(!ok){
        // revert checkbox visually
        e.preventDefault()
        return
      }
    }
    setIncludeCP(willEnable)
  }

  const matched = suggestions.filter(s=> s && s.toLowerCase().includes(query.toLowerCase())).slice(0,10)

  return (
    <div className="searchbar">
      <div className="filters">
        {filters.map(f=> (
          <button key={f} className="filter" onClick={()=>onRemoveFilter(f)}>{f} ✕</button>
        ))}
      </div>
      <div className="controls">
        <input
          value={query}
          onChange={e=>{ setQuery(e.target.value); setOpen(true)}}
          placeholder="Search titles, characters, tags..."
        />
        <div className="controls-vertical">
          <select className="situation-select" value={situationFilter} onChange={e=>setSituationFilter(e.target.value)}>
            <option value="ALL">All situations</option>
            <option value="SOLO">SOLO</option>
            <option value="CP">CP</option>
            <option value="MULTIPLE">MULTIPLE</option>
            <option value="PARODY">PARODY</option>
            <option value="R18">R18</option>
          </select>
          <label className="cp-toggle">
            <input type="checkbox" checked={includeCP} onChange={onToggleCP} />
            CP Mode
          </label>
        </div>
      </div>
      {open && matched.length>0 && (
        <ul className="suggestions">
          {matched.map(s => (
            <li key={s} onClick={()=>{ onAddSuggestion(s); setOpen(false)}}>{s}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
