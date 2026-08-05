import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Global character group management panel.
// Opened from the app header; does not touch item.characters.
export default function CharacterGroupManager({ onClose }) {
  const [groups, setGroups] = useState([])
  const [allChars, setAllChars] = useState([])
  const [collapsed, setCollapsed] = useState({})
  const [newGroupName, setNewGroupName] = useState('')
  const [addingGroup, setAddingGroup] = useState(false)
  const [renaming, setRenaming] = useState(null)     // groupId being renamed
  const [renameVal, setRenameVal] = useState('')
  const [addCharTarget, setAddCharTarget] = useState(null)  // groupId
  const [addCharInput, setAddCharInput] = useState('')
  const [moveState, setMoveState] = useState(null)   // {char, fromGroupId}
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    const [gr, ch] = await Promise.all([
      fetch('/api/character-groups/').then(r => r.json()).catch(() => []),
      fetch('/api/items/all_characters/').then(r => r.json()).catch(() => []),
    ])
    const list = Array.isArray(gr) ? gr : (gr.results || [])
    setGroups(list)
    setCollapsed(Object.fromEntries(list.map(g => [g.id, true])))
    setAllChars(Array.isArray(ch) ? ch : [])
  }, [])

  useEffect(() => { load() }, [load])

  function groupOf(char) {
    return groups.find(g => Array.isArray(g.characters) && g.characters.includes(char)) || null
  }

  const assignedChars = new Set(groups.flatMap(g => g.characters || []))
  const ungrouped = allChars.filter(c => !assignedChars.has(c))

  // --- API helpers ---
  async function apiCall(url, method, body) {
    const r = await fetch(url, { method, headers: HEADERS, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      throw new Error(j.detail || j.name || r.status)
    }
    return r.status === 204 ? null : r.json()
  }

  async function createGroup() {
    const name = newGroupName.trim()
    if (!name) return
    try {
      await apiCall('/api/character-groups/', 'POST', { name, characters: [] })
      setNewGroupName(''); setAddingGroup(false)
      load()
    } catch (e) { alert('作成失敗: ' + e.message) }
  }

  async function renameGroup(g) {
    const name = renameVal.trim()
    if (!name || name === g.name) { setRenaming(null); return }
    try {
      await apiCall(`/api/character-groups/${g.id}/`, 'PATCH', { name })
      setRenaming(null)
      load()
    } catch (e) { alert('リネーム失敗: ' + e.message) }
  }

  async function deleteGroup(g) {
    if (!window.confirm(`グループ「${g.name}」を削除しますか？\nキャラクターの割り当てが解除されます。`)) return
    try {
      await apiCall(`/api/character-groups/${g.id}/`, 'DELETE')
      load()
    } catch (e) { alert('削除失敗: ' + e.message) }
  }

  async function moveCharacter(char, fromGroupId, toGroupId) {
    try {
      await apiCall('/api/character-groups/move_character/', 'POST', {
        character: char, from_group_id: fromGroupId, to_group_id: toGroupId,
      })
      setMoveState(null)
      load()
    } catch (e) { alert('移動失敗: ' + e.message) }
  }

  async function removeFromGroup(char, groupId) {
    const g = groups.find(x => x.id === groupId)
    if (!g) return
    try {
      await apiCall(`/api/character-groups/${groupId}/`, 'PATCH', {
        characters: g.characters.filter(c => c !== char),
      })
      load()
    } catch (e) { alert('削除失敗: ' + e.message) }
  }

  async function addCharToGroup(char, groupId) {
    const trimmed = char.trim()
    if (!trimmed) return
    try {
      await apiCall('/api/character-groups/move_character/', 'POST', {
        character: trimmed, from_group_id: null, to_group_id: groupId,
      })
      setAddCharTarget(null); setAddCharInput('')
      load()
    } catch (e) { alert('追加失敗: ' + e.message) }
  }

  const suggestions = (addCharInput
    ? allChars.filter(c => c.toLowerCase().includes(addCharInput.toLowerCase()))
    : allChars
  ).filter(c => {
    const g = groups.find(x => x.id === addCharTarget)
    return g ? !g.characters.includes(c) : true
  }).slice(0, 10)

  const sortedGroups = [...groups].sort((a, b) => a.name.localeCompare(b.name))

  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const visibleGroups = sortedGroups.filter(g =>
    !searching || g.name.toLowerCase().includes(q) || (g.characters || []).some(c => c.toLowerCase().includes(q))
  )

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>キャラクターグループ管理</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-search">
          <input
            className="cgm-search-input"
            placeholder="グループ名・キャラクター名で検索"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        <div className="cgm-panel-body">
          {/* Groups */}
          {visibleGroups.length === 0 && searching && (
            <div className="cgm-empty-hint">一致するグループ・キャラクターがありません</div>
          )}
          {visibleGroups.map(g => {
            const chars = g.characters || []
            const isCollapsed = searching ? false : collapsed[g.id]
            return (
              <div key={g.id} className="cgm-panel-group">
                <div className="cgm-panel-group-header">
                  <button className="cgm-toggle" onClick={() => setCollapsed(p => ({ ...p, [g.id]: !p[g.id] }))}>
                    {isCollapsed ? '▶' : '▼'}
                  </button>

                  {renaming === g.id ? (
                    <input
                      className="cgm-rename-input"
                      value={renameVal}
                      onChange={e => setRenameVal(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') renameGroup(g); if (e.key === 'Escape') setRenaming(null) }}
                      onBlur={() => renameGroup(g)}
                      autoFocus
                    />
                  ) : (
                    <span className="cgm-panel-group-name" onDoubleClick={() => { setRenaming(g.id); setRenameVal(g.name) }}>
                      {g.name}
                    </span>
                  )}

                  <span className="cgm-group-count">{chars.length}</span>
                  <button className="cgm-icon-btn" title="キャラを追加"
                    onClick={() => { setAddCharTarget(g.id); setAddCharInput('') }}>＋</button>
                  <button className="cgm-icon-btn cgm-icon-rename" title="グループ名を変更"
                    onClick={() => { setRenaming(g.id); setRenameVal(g.name) }}>✎</button>
                  <button className="cgm-icon-btn cgm-icon-delete" title="グループを削除"
                    onClick={() => deleteGroup(g)}>🗑</button>
                </div>

                {!isCollapsed && (
                  <div className="cgm-panel-chips">
                    {chars.map(char => (
                      <span key={char} className="cgm-panel-chip">
                        {char}
                        <button className="cgm-chip-btn" title="グループを変更"
                          onClick={() => setMoveState({ char, fromGroupId: g.id })}>⇄</button>
                        <button className="cgm-chip-btn cgm-chip-del" title="このグループから外す"
                          onClick={() => removeFromGroup(char, g.id)}>×</button>
                      </span>
                    ))}
                    {chars.length === 0 && <span className="cgm-empty-hint">キャラなし</span>}
                  </div>
                )}

                {/* Add char popover for this group */}
                {addCharTarget === g.id && (
                  <AddCharPopover
                    input={addCharInput}
                    setInput={setAddCharInput}
                    suggestions={suggestions}
                    onAdd={c => addCharToGroup(c, g.id)}
                    onClose={() => { setAddCharTarget(null); setAddCharInput('') }}
                  />
                )}
              </div>
            )
          })}

          {/* Ungrouped */}
          {(() => {
            const shownUngrouped = ungrouped.filter(c => !searching || c.toLowerCase().includes(q))
            if (shownUngrouped.length === 0) return null
            return (
            <div className="cgm-panel-group cgm-panel-ungrouped">
              <div className="cgm-panel-group-header">
                <span className="cgm-panel-group-name" style={{ color: '#9ca3af' }}>未分類 ({shownUngrouped.length})</span>
              </div>
              <div className="cgm-panel-chips">
                {shownUngrouped.map(char => (
                  <span key={char} className="cgm-panel-chip cgm-panel-chip-ungrouped">
                    {char}
                    <button className="cgm-chip-btn" title="グループに割り当て"
                      onClick={() => setMoveState({ char, fromGroupId: null })}>⇄</button>
                  </span>
                ))}
              </div>
            </div>
            )
          })()}

          {/* New group */}
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
              <button className="cgm-new-group-btn" onClick={() => setAddingGroup(true)}>
                ＋ グループを新規作成
              </button>
            )}
          </div>
        </div>

        {/* Move popover */}
        {moveState && (
          <div className="cgm-move-overlay" onClick={() => setMoveState(null)}>
            <div className="cgm-move-popup" onClick={e => e.stopPropagation()}>
              <div className="cgm-move-title">「{moveState.char}」を移動</div>
              {sortedGroups.filter(g => g.id !== moveState.fromGroupId).map(g => (
                <button key={g.id} className="cgm-move-option"
                  onClick={() => moveCharacter(moveState.char, moveState.fromGroupId, g.id)}>
                  {g.name}
                </button>
              ))}
              {moveState.fromGroupId !== null && (
                <button className="cgm-move-option cgm-move-ungrouped"
                  onClick={() => moveCharacter(moveState.char, moveState.fromGroupId, null)}>
                  未分類に移動
                </button>
              )}
              <button className="cgm-move-cancel" onClick={() => setMoveState(null)}>キャンセル</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function AddCharPopover({ input, setInput, suggestions, onAdd, onClose }) {
  return (
    <div className="cgm-add-popover">
      <input
        className="cgm-add-input"
        placeholder="キャラクター名"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && input.trim()) onAdd(input.trim())
          if (e.key === 'Escape') onClose()
        }}
        autoFocus
      />
      {suggestions.map(s => (
        <button key={s} className="cgm-add-suggestion" onClick={() => onAdd(s)}>{s}</button>
      ))}
      {input.trim() && !suggestions.some(s => s === input.trim()) && (
        <button className="cgm-add-suggestion cgm-add-new" onClick={() => onAdd(input.trim())}>
          ＋ 「{input.trim()}」を新規追加
        </button>
      )}
      <button className="cgm-add-cancel" onClick={onClose}>キャンセル</button>
    </div>
  )
}
