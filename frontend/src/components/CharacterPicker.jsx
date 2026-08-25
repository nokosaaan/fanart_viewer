import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

// Per-item character picker. Shows characters organized by group.
// When `titles` (the item's currently selected titles) is non-empty, the
// group list is restricted to groups linked to one of those titles — and a
// new group can be created inline, pre-linked to them — so character-group
// naming stays tied to title naming instead of drifting independently.
export default function CharacterPicker({ charList, setCharList, allChars, titles }) {
  const [groups, setGroups] = useState([])
  const [collapsed, setCollapsed] = useState({})
  const [ungroupedCollapsed, setUngroupedCollapsed] = useState(true)
  const [query, setQuery] = useState('')
  const [creatingGroup, setCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

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

  const selectedTitles = Array.isArray(titles) ? titles.filter(Boolean) : []
  const matchingGroups = selectedTitles.length > 0
    ? groups.filter(g => (g.titles || []).some(t => selectedTitles.includes(t)))
    : []
  // Only actually restrict when it would narrow things down. Most existing
  // groups haven't been retroactively linked to a title yet (see
  // CharacterGroupManager) — filtering to zero matches would just make every
  // existing character invisible/unselectable, which is worse than not
  // restricting at all.
  const scoped = selectedTitles.length > 0 && matchingGroups.length > 0
  const visibleGroups = scoped ? matchingGroups : groups

  async function createGroupForTitle() {
    const name = newGroupName.trim()
    if (!name) return
    try {
      const r = await fetch('/api/character-groups/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify({ name, characters: [], titles: selectedTitles }),
      })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j.detail || j.name || r.status)
      }
      setNewGroupName('')
      setCreatingGroup(false)
      loadGroups()
    } catch (e) {
      alert('グループ作成に失敗: ' + e.message)
    }
  }

  // All characters known (union of group members + allChars prop)
  const allGroupChars = groups.flatMap(g => g.characters || [])
  const allKnown = Array.from(new Set([...allGroupChars, ...(allChars || [])]))

  // Characters that exist but aren't in any group
  const groupedChars = new Set(allGroupChars)
  const ungrouped = allKnown.filter(c => !groupedChars.has(c))

  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const exactExists = searching && allKnown.some(c => c.toLowerCase() === q)

  function filterChars(chars) {
    if (!searching) return chars
    return chars.filter(c => c.toLowerCase().includes(q))
  }

  // Characters actually rendered as a clickable chip somewhere below — used
  // to catch already-selected characters that fall outside the current
  // title-scoped group list (or the active search filter) so they don't
  // silently vanish from view with no way to deselect them.
  const shownChars = new Set([
    ...visibleGroups.flatMap(g => filterChars(Array.isArray(g.characters) ? g.characters : [])),
    ...filterChars(ungrouped),
  ])
  const hiddenSelected = charList.filter(c => !shownChars.has(c))

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

      {selectedTitles.length === 0 && (
        <div className="cp-hint">タイトルを選択すると、そのタイトルのキャラクターグループに絞り込まれます</div>
      )}
      {selectedTitles.length > 0 && matchingGroups.length === 0 && (
        <div className="cp-hint">このタイトルに紐づくグループはまだありません（全グループを表示中）。下のボタンから作成できます</div>
      )}

      {visibleGroups.map(g => {
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

      {selectedTitles.length > 0 && (
        creatingGroup ? (
          <div className="cp-new-group-form" style={{ display: 'flex', gap: 6, margin: '4px 0' }}>
            <input
              className="cp-search-input"
              placeholder="新しいグループ名"
              value={newGroupName}
              onChange={e => setNewGroupName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createGroupForTitle(); if (e.key === 'Escape') { setCreatingGroup(false); setNewGroupName('') } }}
              autoFocus
            />
            <button className="btn" onClick={createGroupForTitle}>作成</button>
            <button className="btn" onClick={() => { setCreatingGroup(false); setNewGroupName('') }}>×</button>
          </div>
        ) : (
          <button className="cp-add-group-btn" onClick={() => setCreatingGroup(true)}>
            ＋ このタイトル用のグループを新規作成
          </button>
        )
      )}

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

      {/* Selected characters not currently shown above (hidden by title
          scoping or the active search) — always reachable to deselect. */}
      {hiddenSelected.length > 0 && (
        <div className="cp-extra">
          {hiddenSelected.map(c => (
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
