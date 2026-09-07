import React, { useState, useEffect, useCallback, useMemo } from 'react'

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
  const [allTitles, setAllTitles] = useState([])
  const [collapsed, setCollapsed] = useState({})
  const [newGroupName, setNewGroupName] = useState('')
  const [addingGroup, setAddingGroup] = useState(false)
  const [renaming, setRenaming] = useState(null)     // groupId being renamed
  const [renameVal, setRenameVal] = useState('')
  const [addCharTarget, setAddCharTarget] = useState(null)  // groupId
  const [addCharInput, setAddCharInput] = useState('')
  const [addTitleTarget, setAddTitleTarget] = useState(null)  // groupId
  const [addTitleInput, setAddTitleInput] = useState('')
  // {chars: string[]} — the character(s) currently targeted by the move
  // popover. Always an array so the single-chip ⇄ shortcut and the
  // multi-select "N件を移動" bar share one code path; each character's
  // current group is looked up fresh via groupOf() at move time rather
  // than captured up front, since a checkbox selection can span multiple
  // source groups (and ungrouped) at once.
  const [moveState, setMoveState] = useState(null)
  const [selectedChars, setSelectedChars] = useState(new Set())
  const [query, setQuery] = useState('')
  const [parentPickerFor, setParentPickerFor] = useState(null)  // groupId whose parent is being set

  const load = useCallback(async () => {
    const [gr, ch, ti] = await Promise.all([
      fetch('/api/character-groups/').then(r => r.json()).catch(() => []),
      fetch('/api/items/all_characters/').then(r => r.json()).catch(() => []),
      fetch('/api/items/all_titles/').then(r => r.json()).catch(() => []),
    ])
    const list = Array.isArray(gr) ? gr : (gr.results || [])
    setGroups(list)
    setCollapsed(Object.fromEntries(list.map(g => [g.id, true])))
    setAllChars(Array.isArray(ch) ? ch : [])
    setAllTitles(Array.isArray(ti) ? ti : [])
  }, [])

  useEffect(() => { load() }, [load])

  // O(1) id -> group lookup instead of scanning the whole list on every
  // rename/add-character/add-title/remove action — groups.find(x => x.id
  // === id) was a linear scan repeated on nearly every interaction, which
  // is negligible at a handful of groups but needlessly re-scans the full
  // list on every click as this app's own character/title vocabulary
  // keeps growing. Recomputed only when `groups` itself changes (i.e.
  // after a load()), not on every render.
  const groupsById = useMemo(() => new Map(groups.map(g => [g.id, g])), [groups])

  // Children-by-parent-id index for tree rendering — mirrors Danbooru's own
  // wiki hierarchy (a franchise with narrower sub-titles under it, e.g.
  // Muv-Luv -> Muv-Luv Girls Garden). Recomputed only when `groups` changes.
  const childrenByParentId = useMemo(() => {
    const m = new Map()
    groups.forEach(g => {
      if (g.parent == null) return
      if (!m.has(g.parent)) m.set(g.parent, [])
      m.get(g.parent).push(g)
    })
    m.forEach(list => list.sort((a, b) => a.name.localeCompare(b.name)))
    return m
  }, [groups])

  // All descendant ids of `groupId` — used to keep the parent-picker from
  // even offering a choice the server would reject as a cycle (it still
  // validates server-side too; this is just so the picker itself never
  // shows an obviously-invalid option).
  function descendantIds(groupId) {
    const out = new Set()
    const stack = [...(childrenByParentId.get(groupId) || [])]
    while (stack.length) {
      const g = stack.pop()
      if (out.has(g.id)) continue
      out.add(g.id)
      stack.push(...(childrenByParentId.get(g.id) || []))
    }
    return out
  }

  async function setParent(groupId, parentId) {
    try {
      await apiCall(`/api/character-groups/${groupId}/`, 'PATCH', { parent: parentId })
      setParentPickerFor(null)
      load()
    } catch (e) { alert('親グループの設定に失敗: ' + e.message) }
  }

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

  // Moves one or more characters to `toGroupId` (or null = 未分類). Each
  // character's current group is resolved individually via groupOf() so a
  // multi-select spanning several source groups (or a mix of grouped/
  // ungrouped) works in one action — the backend endpoint only ever moves
  // one character at a time, so this is a sequential loop rather than a
  // single bulk request (fine at this app's scale: tens of characters per
  // group, not thousands).
  async function moveCharacters(chars, toGroupId) {
    try {
      for (const char of chars) {
        const from = groupOf(char)
        await apiCall('/api/character-groups/move_character/', 'POST', {
          character: char, from_group_id: from ? from.id : null, to_group_id: toGroupId,
        })
      }
      setMoveState(null)
      setSelectedChars(new Set())
      load()
    } catch (e) { alert('移動失敗: ' + e.message) }
  }

  function toggleSelectChar(char) {
    setSelectedChars(prev => {
      const next = new Set(prev)
      if (next.has(char)) next.delete(char); else next.add(char)
      return next
    })
  }

  async function removeFromGroup(char, groupId) {
    const g = groupsById.get(groupId)
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

  async function addTitleToGroup(title, groupId) {
    const trimmed = title.trim()
    if (!trimmed) return
    const g = groupsById.get(groupId)
    if (!g || (g.titles || []).includes(trimmed)) { setAddTitleTarget(null); setAddTitleInput(''); return }
    try {
      await apiCall(`/api/character-groups/${groupId}/`, 'PATCH', { titles: [...(g.titles || []), trimmed] })
      setAddTitleTarget(null); setAddTitleInput('')
      load()
    } catch (e) { alert('タイトルの紐づけに失敗: ' + e.message) }
  }

  async function removeTitleFromGroup(title, groupId) {
    const g = groupsById.get(groupId)
    if (!g) return
    try {
      await apiCall(`/api/character-groups/${groupId}/`, 'PATCH', { titles: (g.titles || []).filter(t => t !== title) })
      load()
    } catch (e) { alert('タイトルの解除に失敗: ' + e.message) }
  }

  const suggestions = (addCharInput
    ? allChars.filter(c => c.toLowerCase().includes(addCharInput.toLowerCase()))
    : allChars
  ).filter(c => {
    const g = groupsById.get(addCharTarget)
    return g ? !g.characters.includes(c) : true
  }).slice(0, 10)

  const titleSuggestions = (addTitleInput
    ? allTitles.filter(t => t.toLowerCase().includes(addTitleInput.toLowerCase()))
    : allTitles
  ).filter(t => {
    const g = groupsById.get(addTitleTarget)
    return g ? !(g.titles || []).includes(t) : true
  }).slice(0, 10)

  const sortedGroups = [...groups].sort((a, b) => a.name.localeCompare(b.name))
  // Always the FULL hierarchy, independent of the search box above — the
  // move-target picker's tree (see renderMoveOption) shouldn't hide a
  // group just because it doesn't match whatever's currently typed into
  // the main list's search filter.
  const allTopLevelGroups = sortedGroups.filter(g => g.parent == null)

  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const visibleGroups = sortedGroups.filter(g =>
    !searching || g.name.toLowerCase().includes(q)
    || (g.characters || []).some(c => c.toLowerCase().includes(q))
    || (g.titles || []).some(t => t.toLowerCase().includes(q))
  )
  // Not searching: render as a tree (top-level groups, each followed by its
  // own children indented beneath — mirrors Danbooru's own wiki hierarchy).
  // Searching: keep the existing flat filtered list — a matched child's
  // ancestor chain isn't necessarily also a match, so nesting it under a
  // possibly-hidden parent would be confusing rather than helpful here.
  const topLevelGroups = visibleGroups.filter(g => g.parent == null)

  function renderGroupNode(g, depth) {
    const chars = g.characters || []
    const isCollapsed = searching ? false : collapsed[g.id]
    const children = searching ? [] : (childrenByParentId.get(g.id) || [])
    const excludedForParentPicker = new Set([g.id, ...descendantIds(g.id)])
    return (
      <React.Fragment key={g.id}>
        <div className="cgm-panel-group" style={depth ? { marginLeft: depth * 20 } : undefined}>
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
                {depth > 0 && <span className="cgm-tree-branch">↳ </span>}
                {g.name}
              </span>
            )}

            <span className="cgm-group-count">{chars.length}</span>
            <button className="cgm-icon-btn" title="キャラを追加"
              onClick={() => { setAddCharTarget(g.id); setAddCharInput('') }}>＋</button>
            <button className="cgm-icon-btn" title="親グループを設定(階層構造)"
              onClick={() => setParentPickerFor(g.id)}>🌳</button>
            <button className="cgm-icon-btn cgm-icon-rename" title="グループ名を変更"
              onClick={() => { setRenaming(g.id); setRenameVal(g.name) }}>✎</button>
            <button className="cgm-icon-btn cgm-icon-delete" title="グループを削除"
              onClick={() => deleteGroup(g)}>🗑</button>
          </div>

          {g.parent != null && (
            <div className="cgm-parent-hint">
              親グループ: <strong>{groupsById.get(g.parent)?.name || '(不明)'}</strong>
              <button className="cgm-chip-btn cgm-chip-del" title="親グループを解除"
                onClick={() => setParent(g.id, null)}>×</button>
            </div>
          )}

          {parentPickerFor === g.id && (
            <div className="cgm-add-popover cgm-parent-picker">
              <div className="cgm-parent-picker-hint">親グループを選択(タイトルの階層構造 — 例: Fate → Fate/strange Fake):</div>
              <div className="cgm-parent-picker-options">
                {sortedGroups
                  .filter(other => other.parent == null && !excludedForParentPicker.has(other.id))
                  .map(other => renderParentOption(other, 0, g.id, excludedForParentPicker))}
              </div>
              <button className="cgm-add-cancel" onClick={() => setParentPickerFor(null)}>キャンセル</button>
            </div>
          )}

          {/* Titles this group belongs to — always visible (not gated by
              collapse) so inconsistent/missing links are easy to spot
              and fix while scanning the full list. */}
          <div className="cgm-panel-titles">
            {(g.titles || []).map(t => (
              <span key={t} className="cgm-title-chip">
                {t}
                <button className="cgm-chip-btn cgm-chip-del" title="このタイトルとの紐づけを解除"
                  onClick={() => removeTitleFromGroup(t, g.id)}>×</button>
              </span>
            ))}
            <button className="cgm-icon-btn" title="タイトルを紐づけ"
              onClick={() => { setAddTitleTarget(g.id); setAddTitleInput('') }}>＋タイトル</button>
            {(g.titles || []).length === 0 && <span className="cgm-empty-hint">タイトル未設定</span>}
            {addTitleTarget === g.id && (
              <AddCharPopover
                placeholder="タイトル名"
                input={addTitleInput}
                setInput={setAddTitleInput}
                suggestions={titleSuggestions}
                onAdd={t => addTitleToGroup(t, g.id)}
                onClose={() => { setAddTitleTarget(null); setAddTitleInput('') }}
              />
            )}
          </div>

          {!isCollapsed && (
            <div className="cgm-panel-chips">
              {chars.map(char => (
                <span key={char} className={`cgm-panel-chip${selectedChars.has(char) ? ' cgm-panel-chip-selected' : ''}`}>
                  <input type="checkbox" className="cgm-chip-checkbox" title="複数選択して一括移動"
                    checked={selectedChars.has(char)} onChange={() => toggleSelectChar(char)} />
                  {char}
                  <button className="cgm-chip-btn" title="グループを変更"
                    onClick={() => setMoveState({ chars: [char] })}>⇄</button>
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
        {/* Child groups nest under their parent's collapse state too — a
            closed parent (▶) hides its children along with its own
            character chips, instead of always showing the full subtree
            regardless of toggle state. */}
        {!isCollapsed && children.map(child => renderGroupNode(child, depth + 1))}
      </React.Fragment>
    )
  }

  // Parent-picker popover's own tree render (see the 🌳 popover below) —
  // same nesting/collapse-reuse idea as renderMoveOption, but a node (and
  // therefore its whole subtree, never just part of it — see
  // excludedForParentPicker's own comment) can be excluded entirely: the
  // group having its parent set can't become its own ancestor, so it and
  // everything under it are skipped rather than rendered disabled.
  function renderParentOption(other, depth, targetGroupId, excludedSet) {
    if (excludedSet.has(other.id)) return null
    const children = (childrenByParentId.get(other.id) || []).filter(c => !excludedSet.has(c.id))
    const isCollapsed = collapsed[other.id]
    return (
      <React.Fragment key={other.id}>
        <div style={{ display: 'flex', alignItems: 'center', paddingLeft: depth ? depth * 16 : 0 }}>
          {children.length > 0 ? (
            <button className="cgm-toggle" onClick={() => setCollapsed(p => ({ ...p, [other.id]: !p[other.id] }))}>
              {isCollapsed ? '▶' : '▼'}
            </button>
          ) : (
            <span style={{ display: 'inline-block', width: 14, flexShrink: 0 }} />
          )}
          <button className="cgm-add-suggestion" style={{ flex: 1 }} onClick={() => setParent(targetGroupId, other.id)}>
            {other.name}
          </button>
        </div>
        {!isCollapsed && children.map(child => renderParentOption(child, depth + 1, targetGroupId, excludedSet))}
      </React.Fragment>
    )
  }

  // Move-target picker's own tree render (see the popover below) — same
  // parent/child nesting as renderGroupNode, and reuses that SAME
  // `collapsed` state (keyed by group id) so a branch already collapsed
  // in the main list starts collapsed here too, rather than tracking a
  // second, independent collapse state for this one-shot picker.
  function renderMoveOption(g, depth) {
    const children = childrenByParentId.get(g.id) || []
    const isCollapsed = collapsed[g.id]
    return (
      <React.Fragment key={g.id}>
        <div style={{ display: 'flex', alignItems: 'center', paddingLeft: depth ? depth * 16 : 0 }}>
          {children.length > 0 ? (
            <button className="cgm-toggle" onClick={() => setCollapsed(p => ({ ...p, [g.id]: !p[g.id] }))}>
              {isCollapsed ? '▶' : '▼'}
            </button>
          ) : (
            <span style={{ display: 'inline-block', width: 14, flexShrink: 0 }} />
          )}
          <button className="cgm-move-option" style={{ flex: 1 }}
            onClick={() => moveCharacters(moveState.chars, g.id)}>
            {g.name}
          </button>
        </div>
        {!isCollapsed && children.map(child => renderMoveOption(child, depth + 1))}
      </React.Fragment>
    )
  }

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

        {selectedChars.size > 0 && (
          <div className="cgm-panel-search cgm-select-bar" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12 }}>{selectedChars.size}件選択中</span>
            <button className="btn" style={{ fontSize: 12 }}
              onClick={() => setMoveState({ chars: [...selectedChars] })}>
              選択した{selectedChars.size}件を移動
            </button>
            <button className="btn" style={{ fontSize: 12 }}
              onClick={() => setSelectedChars(new Set())}>
              選択解除
            </button>
          </div>
        )}

        <div className="cgm-panel-body">
          {/* Groups */}
          {visibleGroups.length === 0 && searching && (
            <div className="cgm-empty-hint">一致するグループ・キャラクターがありません</div>
          )}
          {(searching ? visibleGroups : topLevelGroups).map(g => renderGroupNode(g, 0))}

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
                  <span key={char} className={`cgm-panel-chip cgm-panel-chip-ungrouped${selectedChars.has(char) ? ' cgm-panel-chip-selected' : ''}`}>
                    <input type="checkbox" className="cgm-chip-checkbox" title="複数選択して一括移動"
                      checked={selectedChars.has(char)} onChange={() => toggleSelectChar(char)} />
                    {char}
                    <button className="cgm-chip-btn" title="グループに割り当て"
                      onClick={() => setMoveState({ chars: [char] })}>⇄</button>
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

        {/* Move popover — moveState.chars is always an array, whether opened
            from a single chip's ⇄ shortcut or the multi-select bar below,
            so this one popup handles both. Each character keeps its own
            current group looked up fresh in moveCharacters(), so a mixed
            selection (spanning several groups, or grouped+ungrouped) still
            resolves correctly per-character rather than assuming one
            shared source group. */}
        {moveState && (
          <div className="cgm-move-overlay" onClick={() => setMoveState(null)}>
            <div className="cgm-move-popup" onClick={e => e.stopPropagation()}>
              <div className="cgm-move-title">
                {moveState.chars.length === 1
                  ? `「${moveState.chars[0]}」を移動`
                  : `選択した${moveState.chars.length}件を移動`}
              </div>
              <div className="cgm-move-options">
                {allTopLevelGroups.map(g => renderMoveOption(g, 0))}
                <button className="cgm-move-option cgm-move-ungrouped"
                  onClick={() => moveCharacters(moveState.chars, null)}>
                  未分類に移動
                </button>
              </div>
              <button className="cgm-move-cancel" onClick={() => setMoveState(null)}>キャンセル</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function AddCharPopover({ input, setInput, suggestions, onAdd, onClose, placeholder = 'キャラクター名' }) {
  return (
    <div className="cgm-add-popover">
      <input
        className="cgm-add-input"
        placeholder={placeholder}
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
