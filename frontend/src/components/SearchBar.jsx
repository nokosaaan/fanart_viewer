import React, {useState} from 'react'

export default function SearchBar({query, setQuery, suggestions, onAddSuggestion, filters, onRemoveFilter, includeCP, setIncludeCP, includeR18, setIncludeR18, previewOpen, setPreviewOpen, situationFilter, setSituationFilter}){
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

  function onToggleR18(e){
    const willEnable = e.target.checked
    if(willEnable){
      const ok = window.confirm('閲覧注意: R18 コンテンツが表示されます。表示を続けてもよいですか？')
      if(!ok){
        e.preventDefault()
        return
      }
    }
    setIncludeR18(willEnable)
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
          <div className="options-panel">
            <div className="situation-options">
              {[
                { v: 'ALL', l: 'All' },
                { v: 'SOLO', l: 'Solo' },
                { v: 'CP', l: 'CP' },
                { v: 'MULTIPLE', l: 'Multiple' },
                { v: 'PARODY', l: 'Parody' },
                // R18 option is only shown when user has enabled R18 mode
                ...(includeR18 ? [{ v: 'R18', l: 'R18' }] : [])
              ].map(o => (
                <button
                  key={o.v}
                  className={`situation-option ${situationFilter === o.v ? 'active' : ''}`}
                  onClick={() => setSituationFilter(o.v)}
                >
                  {o.l}
                </button>
              ))}
            </div>
            <div>
              <button
                className={`cp-chip ${includeCP ? 'enabled' : ''}`}
                onClick={() => {
                  const willEnable = !includeCP
                  if (willEnable) {
                    const ok = window.confirm('閲覧注意: CP（カップリング）を含む可能性のあるコンテンツが表示されます。表示を続けてもよいですか？')
                    if (!ok) return
                  }
                  setIncludeCP(willEnable)
                }}
              >
                {includeCP ? 'CP: ON' : 'CP: OFF'}
              </button>
              <button
                style={{marginLeft:8}}
                className={`cp-chip ${includeR18 ? 'enabled' : ''}`}
                onClick={() => {
                  const willEnable = !includeR18
                  if (willEnable) {
                    const ok = window.confirm('閲覧注意: R18 コンテンツが表示されます。表示を続けてもよいですか？')
                    if (!ok) return
                  }
                  setIncludeR18(willEnable)
                }}
              >
                {includeR18 ? 'R18: ON' : 'R18: OFF'}
              </button>
            </div>
          </div>
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
