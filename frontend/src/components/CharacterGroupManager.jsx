import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// CharacterGroupManager
// Props:
//   charList       string[]   — current characters assigned to this item
//   setCharList    fn         — update item's character list
//   allChars       string[]   — all known character names (for suggestions)
export default function CharacterGroupManager({ charList, setCharList, allChars }) {
  const [groups, setGroups] = useState([])          // [{id, name, characters:[]}]
  const [collapsed, setCollapsed] = useState({})    // {groupId: bool}
  const [newGroupName, setNewGroupName] = useState('')
  const [addingGroup, setAddingGroup] = useState(false)
  const [moveMenu, setMoveMenu] = useState(null)    // {char, fromGroupId}
  const [addCharMenu, setAddCharMenu] = useState(null)  // groupId | 'ungrouped'
  const [addCharInput, setAddCharInput] = useState('')
  const [ungroupedCollapsed, setUngroupedCollapsed] = useState(false)

  const loadGroups = useCallback(async () => {
    try {
      const r = await fetch('/api/character-groups/')
      if (!r.ok) return
      const data = await r.json()
      const list = Array.isArray(data) ? data : (data.results || [])
      setGroups(list)
    } catch (e) {
      console.error('Failed to load character groups', e)
    }
  }, [])

  useEffect(() => { loadGroups() }, [loadGroups])

  // Close menus on outside click
  useEffect(() => {
    if (!moveMenu && addCharMenu === null) return
    function handler(e) {
      if (!e.target.closest('.cgm-menu')) {
        setMoveMenu(null)
        setAddCharMenu(null)
        setAddCharInput('')
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [moveMenu, addCharMenu])

  function groupOf(char) {
    return groups.find(g => Array.isArray(g.characters) && g.characters.includes(char)) || null
  }

  // Characters in this item that belong to a known group
  const assignedChars = charList.filter(c => groupOf(c) !== null)
  // Characters in this item with no group
  const ungroupedChars = charList.filter(c => groupOf(c) === null)

  // Groups that have at least one character present in this item
  const activeGroups = groups.filter(g =>
    Array.isArray(g.characters) && g.characters.some(c => charList.includes(c))
  )
  // Groups that have no characters in this item (for move targets)
  const allGroupsSorted = [...groups].sort((a, b) => a.name.localeCompare(b.name))

  async function createGroup() {
    const name = newGroupName.trim()
    if (!name) return
    try {
      const r = await fetch('/api/character-groups/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify({ name, characters: [] }),
      })
      if (!r.ok) { const j = await r.json().catch(() => ({})); alert('Failed: ' + (j.name || j.detail || r.status)); return }
      setNewGroupName('')
      setAddingGroup(false)
      await loadGroups()
    } catch (e) { alert('Failed to create group: ' + e.message) }
  }

  async function deleteGroup(g) {
    if (!window.confirm(`グループ「${g.name}」を削除しますか？\nキャラクター割り当ては解除されます。`)) return
    try {
      await fetch(`/api/character-groups/${g.id}/`, {
        method: 'DELETE', headers: HEADERS, credentials: 'same-origin',
      })
      await loadGroups()
    } catch (e) { alert('削除に失敗しました') }
  }

  async function moveCharacter(char, fromGroupId, toGroupId) {
    try {
      const r = await fetch('/api/character-groups/move_character/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify({ character: char, from_group_id: fromGroupId, to_group_id: toGroupId }),
      })
      if (!r.ok) { const j = await r.json().catch(() => ({})); alert('移動失敗: ' + (j.detail || r.status)); return }
      await loadGroups()
    } catch (e) { alert('移動失敗: ' + e.message) }
    setMoveMenu(null)
  }

  async function addCharToGroup(char, groupId) {
    const trimmed = char.trim()
    if (!trimmed) return
    // Add to item list if not present
    if (!charList.includes(trimmed)) setCharList(prev => [...prev, trimmed])
    // Assign to group
    if (groupId !== 'ungrouped') {
      await moveCharacter(trimmed, null, groupId)
    }
    setAddCharMenu(null)
    setAddCharInput('')
  }

  function removeCharFromItem(char) {
    setCharList(prev => prev.filter(c => c !== char))
  }

  function toggleGroup(id) {
    setCollapsed(prev => ({ ...prev, [id]: !prev[id] }))
  }

  // Suggestions: allChars filtered to not-yet-in-item
  const suggestions = (allChars || []).filter(c => !charList.includes(c))

  return (
    <div className="cgm-root">
      {/* Groups with item characters */}
      {activeGroups.map(g => {
        const chars = g.characters.filter(c => charList.includes(c))
        const isCollapsed = collapsed[g.id]
        return (
          <div key={g.id} className="cgm-group">
            <div className="cgm-group-header">
              <button className="cgm-toggle" onClick={() => toggleGroup(g.id)}>
                {isCollapsed ? '▶' : '▼'}
              </button>
              <span className="cgm-group-name">{g.name}</span>
              <span className="cgm-group-count">{chars.length}</span>
              <button className="cgm-add-char-btn" title="このグループにキャラを追加"
                onClick={() => { setAddCharMenu(g.id); setAddCharInput('') }}>＋</button>
              <button className="cgm-del-group-btn" title="グループを削除" onClick={() => deleteGroup(g)}>🗑</button>
            </div>
            {!isCollapsed && (
              <div className="cgm-chips">
                {chars.map(char => (
                  <span key={char} className="cgm-chip">
                    {char}
                    <button className="cgm-chip-move" title="グループを変更"
                      onClick={() => setMoveMenu({ char, fromGroupId: g.id })}>⇄</button>
                    <button className="cgm-chip-remove" onClick={() => removeCharFromItem(char)}>×</button>
                  </span>
                ))}
              </div>
            )}
            {addCharMenu === g.id && (
              <AddCharPopup
                input={addCharInput}
                setInput={setAddCharInput}
                suggestions={suggestions}
                onAdd={c => addCharToGroup(c, g.id)}
                onClose={() => { setAddCharMenu(null); setAddCharInput('') }}
              />
            )}
          </div>
        )
      })}

      {/* Ungrouped characters */}
      {ungroupedChars.length > 0 && (
        <div className="cgm-group cgm-ungrouped">
          <div className="cgm-group-header">
            <button className="cgm-toggle" onClick={() => setUngroupedCollapsed(p => !p)}>
              {ungroupedCollapsed ? '▶' : '▼'}
            </button>
            <span className="cgm-group-name" style={{ color: '#888' }}>未分類</span>
            <span className="cgm-group-count">{ungroupedChars.length}</span>
            <button className="cgm-add-char-btn" title="未分類にキャラを追加"
              onClick={() => { setAddCharMenu('ungrouped'); setAddCharInput('') }}>＋</button>
          </div>
          {!ungroupedCollapsed && (
            <div className="cgm-chips">
              {ungroupedChars.map(char => (
                <span key={char} className="cgm-chip cgm-chip-ungrouped">
                  {char}
                  <button className="cgm-chip-move" title="グループに割り当て"
                    onClick={() => setMoveMenu({ char, fromGroupId: null })}>⇄</button>
                  <button className="cgm-chip-remove" onClick={() => removeCharFromItem(char)}>×</button>
                </span>
              ))}
            </div>
          )}
          {addCharMenu === 'ungrouped' && (
            <AddCharPopup
              input={addCharInput}
              setInput={setAddCharInput}
              suggestions={suggestions}
              onAdd={c => addCharToGroup(c, 'ungrouped')}
              onClose={() => { setAddCharMenu(null); setAddCharInput('') }}
            />
          )}
        </div>
      )}

      {/* Empty item — quick add button */}
      {charList.length === 0 && addCharMenu === null && (
        <div style={{ padding: '4px 0' }}>
          <button className="cgm-add-char-btn" onClick={() => { setAddCharMenu('ungrouped'); setAddCharInput('') }}>
            ＋ キャラクターを追加
          </button>
        </div>
      )}
      {charList.length > 0 && addCharMenu === null && ungroupedChars.length === 0 && activeGroups.length === 0 && (
        <div style={{ padding: '4px 0' }}>
          <button className="cgm-add-char-btn" onClick={() => { setAddCharMenu('ungrouped'); setAddCharInput('') }}>
            ＋ キャラクターを追加
          </button>
        </div>
      )}

      {/* New group creation row */}
      <div className="cgm-new-group-row">
        {addingGroup ? (
          <div className="cgm-new-group-form">
            <input
              className="cgm-new-group-input"
              placeholder="新しいグループ名"
              value={newGroupName}
              onChange={e => setNewGroupName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createGroup(); if (e.key === 'Escape') { setAddingGroup(false); setNewGroupName('') } }}
              autoFocus
            />
            <button className="btn" onClick={createGroup}>作成</button>
            <button className="btn" onClick={() => { setAddingGroup(false); setNewGroupName('') }}>キャンセル</button>
          </div>
        ) : (
          <button className="cgm-new-group-btn" onClick={() => setAddingGroup(true)}>＋ グループを新規作成</button>
        )}
      </div>

      {/* All groups (empty ones — for move targets) — shown as collapsed */}
      {allGroupsSorted.filter(g => !g.characters.some(c => charList.includes(c))).length > 0 && (
        <div className="cgm-empty-groups">
          <span className="cgm-empty-groups-label">他のグループ:</span>
          {allGroupsSorted.filter(g => !g.characters.some(c => charList.includes(c))).map(g => (
            <span key={g.id} className="cgm-empty-group-chip">
              {g.name}
              <button className="cgm-add-char-btn" style={{ marginLeft: 4 }} title={`「${g.name}」にキャラを追加`}
                onClick={() => { setAddCharMenu(g.id); setAddCharInput('') }}>＋</button>
              <button className="cgm-del-group-btn" title="グループを削除" onClick={() => deleteGroup(g)}>🗑</button>
              {addCharMenu === g.id && (
                <AddCharPopup
                  input={addCharInput}
                  setInput={setAddCharInput}
                  suggestions={suggestions}
                  onAdd={c => addCharToGroup(c, g.id)}
                  onClose={() => { setAddCharMenu(null); setAddCharInput('') }}
                />
              )}
            </span>
          ))}
        </div>
      )}

      {/* Move popover */}
      {moveMenu && (
        <div className="cgm-menu cgm-move-menu">
          <div className="cgm-move-title">「{moveMenu.char}」を移動</div>
          {allGroupsSorted.filter(g => g.id !== moveMenu.fromGroupId).map(g => (
            <button key={g.id} className="cgm-move-option"
              onClick={() => moveCharacter(moveMenu.char, moveMenu.fromGroupId, g.id)}>
              {g.name}
            </button>
          ))}
          {moveMenu.fromGroupId !== null && (
            <button className="cgm-move-option cgm-move-option-ungrouped"
              onClick={() => moveCharacter(moveMenu.char, moveMenu.fromGroupId, null)}>
              未分類に移動
            </button>
          )}
          <button className="cgm-move-cancel" onClick={() => setMoveMenu(null)}>キャンセル</button>
        </div>
      )}
    </div>
  )
}

function AddCharPopup({ input, setInput, suggestions, onAdd, onClose }) {
  const filtered = suggestions.filter(s => !input || s.toLowerCase().includes(input.toLowerCase())).slice(0, 12)

  return (
    <div className="cgm-menu cgm-add-popup">
      <input
        className="cgm-add-input"
        placeholder="キャラクター名"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && input.trim()) onAdd(input.trim()); if (e.key === 'Escape') onClose() }}
        autoFocus
      />
      {filtered.map(s => (
        <button key={s} className="cgm-add-suggestion" onClick={() => onAdd(s)}>{s}</button>
      ))}
      {input.trim() && !suggestions.includes(input.trim()) && (
        <button className="cgm-add-suggestion cgm-add-new" onClick={() => onAdd(input.trim())}>
          ＋ 「{input.trim()}」を新規追加
        </button>
      )}
    </div>
  )
}
