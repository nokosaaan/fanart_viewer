import React, {useState} from 'react'
import { DEFAULT_SITUATIONS } from '../lib/situations'

export default function SearchBar({query, setQuery, suggestions, onAddSuggestion, filters, onRemoveFilter, includeCP, setIncludeCP, includeR18, setIncludeR18, situationFilter, setSituationFilter, titleMissingOnly, setTitleMissingOnly, previewMissingOnly, setPreviewMissingOnly, multiImageOnly, setMultiImageOnly, readOnly, situations = DEFAULT_SITUATIONS}){
  const [open, setOpen] = useState(false)

  // Empty query would otherwise match every suggestion (''.includes('') is
  // always true), so clearing the box left the full candidate list showing
  // instead of hiding it -- require at least one real character first.
  const matched = query.trim().length === 0
    ? []
    : suggestions.filter(s=> s && s.toLowerCase().includes(query.toLowerCase())).slice(0,10)

  // Dynamically generated situation options from all current and custom situations
  const situationOptions = [
    { v: 'ALL', l: 'All' },
    ...situations
      .filter(s => {
        if (!includeR18 && s === 'R18' && situationFilter !== 'R18') return false
        if (!includeCP && s === 'CP' && situationFilter !== 'CP') return false
        return true
      })
      .map(s => {
        const l = s === 'CP' || s === 'R18' ? s : (s.charAt(0) + s.slice(1).toLowerCase())
        return { v: s, l }
      })
  ]

  return (
    <div className="searchbar">
      <div className="filters">
        {filters.map(f=> (
          <button key={f} className="filter" onClick={()=>onRemoveFilter(f)}>{f} ✕</button>
        ))}
      </div>
      <div className="controls">
        <input
          data-tour="search-input"
          value={query}
          onChange={e=>{ setQuery(e.target.value); setOpen(true)}}
          placeholder="Search titles, characters, tags..."
        />
        <div className="controls-vertical">
          <div className="options-panel">
            <div className="situation-options">
              {situationOptions.map(o => (
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
                data-tour="filter-title-missing"
                className={`cp-chip ${titleMissingOnly ? 'enabled' : ''}`}
                onClick={() => setTitleMissingOnly(v => !v)}
              >
                {titleMissingOnly ? 'Title: MISSING' : 'Title: ANY'}
              </button>
              <button
                data-tour="filter-preview-missing"
                className={`cp-chip ${previewMissingOnly ? 'enabled' : ''}`}
                onClick={() => setPreviewMissingOnly(v => !v)}
                title="プレビュー画像が無いアイテムだけ表示 — 未取得のアイテムをまとめて見つけたいときに"
              >
                {previewMissingOnly ? 'Preview: MISSING' : 'Preview: ANY'}
              </button>
              <button
                className={`cp-chip ${multiImageOnly ? 'enabled' : ''}`}
                onClick={() => setMultiImageOnly && setMultiImageOnly(v => !v)}
                title="2枚以上のプレビュー画像を持つアイテムだけ表示 — 余計な画像を持つアイテムの確認・整理に"
              >
                {multiImageOnly ? '複数画像: ON' : '複数画像: ANY'}
              </button>
              <button
                className={`cp-chip ${includeCP ? 'enabled' : ''}`}
                onClick={() => setIncludeCP(v => !v)}
                title={includeCP ? 'CPを含むアイテムを表示中（クリックで非表示）' : 'CPを含むアイテムを非表示中（クリックで表示）'}
              >
                {includeCP ? 'CP: ON' : 'CP: OFF'}
              </button>
              {!readOnly && (
                <button
                  style={{marginLeft:8}}
                  className={`cp-chip ${includeR18 ? 'enabled' : ''}`}
                  onClick={() => setIncludeR18(v => !v)}
                  title={includeR18 ? 'R18を含むアイテムを表示中（クリックで非表示）' : 'R18を含むアイテムを非表示中（クリックで表示）'}
                >
                  {includeR18 ? 'R18: ON' : 'R18: OFF'}
                </button>
              )}
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
