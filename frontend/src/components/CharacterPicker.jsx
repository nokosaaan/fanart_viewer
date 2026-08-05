import React, { useState, useEffect, useCallback } from 'react'

// Per-item character picker. Shows characters organized by group.
// Does NOT modify groups — group management is in CharacterGroupManager.
export default function CharacterPicker({ charList, setCharList, allChars }) {
  const [groups, setGroups] = useState([])
  const [collapsed, setCollapsed] = useState({})
  const [ungroupedCollapsed, setUngroupedCollapsed] = useState(true)
  const [query, setQuery] = useState('')

  const loadGroups = useCallback(async () => {
    try {
      const r = await fetch('/api/character-groups/')
      if (!r.ok) return
      const data = await r.json()
      const list = Array.isArray(data) ? data : (data.results || [])
      setGroups(list)
      setCollapsed(Object.fromEntries(list.map(g => [g.id, true])))
    } catch (e) {
      console.error('Failed to load character groups', e)
    }
  }, [])

  useEffect(() => { loadGroups() }, [loadGroups])

  function toggle(char) {
    if (charList.includes(char)) {
      setCharList(prev => prev.filter(c => c !== char))
    } else {
      setCharList(prev => [...prev, char])
    }
  }

  function addNew(raw) {
    const t = raw.trim()
    if (!t) return
    if (!charList.includes(t)) setCharList(prev => [...prev, t])
    setQuery('')
  }

  function groupOf(char) {
    return groups.find(g => Array.isArray(g.characters) && g.characters.includes(char)) || null
  }

  // All characters known (union of group members + allChars prop)
  const allGroupChars = groups.flatMap(g => g.characters || [])
  const allKnown = Array.from(new Set([...allGroupChars, ...(allChars || [])]))

  // Characters in groups
  const groupedChars = new Set(allGroupChars)
  // Characters that exist but aren't in any group
  const ungrouped = allKnown.filter(c => !groupedChars.has(c))

  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const exactExists = searching && allKnown.some(c => c.toLowerCase() === q)

  function filterChars(chars) {
    if (!searching) return chars
    return chars.filter(c => c.toLowerCase().includes(q))
  }

  return (
    <div className="cp-root">
      <div className="cp-search-row">
        <input
          className="cp-search-input"
          placeholder="キャラクター名で検索 / 新規追加"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && searching && !exactExists) addNew(query) }}
        />
        {searching && !exactExists && (
          <button className="btn" onClick={() => addNew(query)}>＋ 追加</button>
        )}
      </div>

      {groups.map(g => {
        const chars = filterChars(Array.isArray(g.characters) ? g.characters : [])
        if (chars.length === 0) return null
        const isCollapsed = searching ? false : collapsed[g.id]
        const selectedInGroup = chars.filter(c => charList.includes(c)).length
        return (
          <div key={g.id} className="cp-group">
            <button className="cp-group-header" onClick={() => setCollapsed(p => ({ ...p, [g.id]: !p[g.id] }))}>
              <span className="cp-toggle">{isCollapsed ? '▶' : '▼'}</span>
              <span className="cp-group-name">{g.name}</span>
              {selectedInGroup > 0 && <span className="cp-selected-badge">{selectedInGroup}</span>}
            </button>
            {!isCollapsed && (
              <div className="cp-chips">
                {chars.map(char => (
                  <button
                    key={char}
                    className={`cp-chip${charList.includes(char) ? ' cp-chip-on' : ''}`}
                    onClick={() => toggle(char)}
                  >{char}</button>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {(() => {
        const shownUngrouped = filterChars(ungrouped)
        if (shownUngrouped.length === 0) return null
        const isCollapsed = searching ? false : ungroupedCollapsed
        return (
          <div className="cp-group">
            <button className="cp-group-header" onClick={() => setUngroupedCollapsed(p => !p)}>
              <span className="cp-toggle">{isCollapsed ? '▶' : '▼'}</span>
              <span className="cp-group-name" style={{ color: '#6b7280' }}>グループなし</span>
              {charList.filter(c => shownUngrouped.includes(c)).length > 0 &&
                <span className="cp-selected-badge">{charList.filter(c => shownUngrouped.includes(c)).length}</span>}
            </button>
            {!isCollapsed && (
              <div className="cp-chips">
                {shownUngrouped.map(char => (
                  <button
                    key={char}
                    className={`cp-chip${charList.includes(char) ? ' cp-chip-on' : ''}`}
                    onClick={() => toggle(char)}
                  >{char}</button>
                ))}
              </div>
            )}
          </div>
        )
      })()}

      {/* Selected summary (characters not in any group) */}
      {charList.filter(c => !allKnown.includes(c)).length > 0 && (
        <div className="cp-extra">
          {charList.filter(c => !allKnown.includes(c)).map(c => (
            <span key={c} className="cp-chip cp-chip-on" style={{ cursor: 'default' }}>
              {c}
              <button className="cp-chip-remove" onClick={() => setCharList(prev => prev.filter(x => x !== c))}>×</button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
