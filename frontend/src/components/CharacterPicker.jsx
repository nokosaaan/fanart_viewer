import React, { useState, useEffect, useCallback } from 'react'

// Per-item character picker. Shows characters organized by group.
// Does NOT modify groups — group management is in CharacterGroupManager.
export default function CharacterPicker({ charList, setCharList, allChars }) {
  const [groups, setGroups] = useState([])
  const [collapsed, setCollapsed] = useState({})
  const [ungroupedCollapsed, setUngroupedCollapsed] = useState(true)
  const [newCharInput, setNewCharInput] = useState('')
  const [showNewInput, setShowNewInput] = useState(false)

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

  function addNew() {
    const t = newCharInput.trim()
    if (!t) return
    if (!charList.includes(t)) setCharList(prev => [...prev, t])
    setNewCharInput('')
    setShowNewInput(false)
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

  // Filter suggestions for new input
  const suggestions = allKnown.filter(c =>
    newCharInput && c.toLowerCase().includes(newCharInput.toLowerCase()) && !charList.includes(c)
  ).slice(0, 8)

  return (
    <div className="cp-root">
      {groups.map(g => {
        const chars = Array.isArray(g.characters) ? g.characters : []
        if (chars.length === 0) return null
        const isCollapsed = collapsed[g.id]
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

      {ungrouped.length > 0 && (
        <div className="cp-group">
          <button className="cp-group-header" onClick={() => setUngroupedCollapsed(p => !p)}>
            <span className="cp-toggle">{ungroupedCollapsed ? '▶' : '▼'}</span>
            <span className="cp-group-name" style={{ color: '#6b7280' }}>グループなし</span>
            {charList.filter(c => ungrouped.includes(c)).length > 0 &&
              <span className="cp-selected-badge">{charList.filter(c => ungrouped.includes(c)).length}</span>}
          </button>
          {!ungroupedCollapsed && (
            <div className="cp-chips">
              {ungrouped.map(char => (
                <button
                  key={char}
                  className={`cp-chip${charList.includes(char) ? ' cp-chip-on' : ''}`}
                  onClick={() => toggle(char)}
                >{char}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* New character */}
      {showNewInput ? (
        <div className="cp-new-row">
          <input
            className="cp-new-input"
            placeholder="キャラクター名"
            value={newCharInput}
            onChange={e => setNewCharInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addNew(); if (e.key === 'Escape') { setShowNewInput(false); setNewCharInput('') } }}
            autoFocus
          />
          <button className="btn" onClick={addNew}>追加</button>
          <button className="btn" onClick={() => { setShowNewInput(false); setNewCharInput('') }}>×</button>
          {suggestions.length > 0 && (
            <div className="cp-suggestions">
              {suggestions.map(s => (
                <button key={s} className="cp-suggestion" onClick={() => { toggle(s); setShowNewInput(false); setNewCharInput('') }}>{s}</button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <button className="cp-add-btn" onClick={() => setShowNewInput(true)}>＋ キャラクターを追加</button>
      )}

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
