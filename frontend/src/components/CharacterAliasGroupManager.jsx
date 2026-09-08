import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

async function apiCall(url, method, body) {
  const r = await fetch(url, { method, headers: HEADERS, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined })
  if (!r.ok) {
    const j = await r.json().catch(() => ({}))
    throw new Error(j.detail || JSON.stringify(j) || r.status)
  }
  return r.status === 204 ? null : r.json()
}

// Reviews candidate name-sets mined from Item.character_regions boxes that
// already carry 2+ character names (see ItemViewSet 経由の
// CharacterAliasGroupViewSet.candidates) — a human decides per candidate
// whether the names are the SAME person under two valid names (e.g. a
// magical girl's real name + transformed name; リンクする) or two different
// people that a detection box happened to merge (リンクしない). Linking
// makes train_character_classifier.py train on that box instead of
// skipping it, and makes views.py's classifier prediction surface every
// linked name together at inference (see CharacterAliasGroup's own
// docstring for the full mechanism).
export default function CharacterAliasGroupManager({ onClose }) {
  const [candidates, setCandidates] = useState([])
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [busyKey, setBusyKey] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [candRes, groupsRes] = await Promise.all([
        fetch('/api/character-alias-groups/candidates/').then(r => r.json()).catch(() => ({})),
        fetch('/api/character-alias-groups/').then(r => r.json()).catch(() => []),
      ])
      setCandidates(candRes.results || [])
      setGroups(Array.isArray(groupsRes) ? groupsRes : (groupsRes.results || []))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function decide(characters, linked) {
    const key = characters.join('|')
    setBusyKey(key)
    try {
      await apiCall('/api/character-alias-groups/', 'POST', { characters, linked })
      await load()
    } catch (e) {
      alert('保存に失敗: ' + e.message)
    } finally {
      setBusyKey(null)
    }
  }

  async function undo(group) {
    setBusyKey(`undo-${group.id}`)
    try {
      await apiCall(`/api/character-alias-groups/${group.id}/`, 'DELETE')
      await load()
    } catch (e) {
      alert('削除に失敗: ' + e.message)
    } finally {
      setBusyKey(null)
    }
  }

  const linkedGroups = groups.filter(g => g.linked)
  const rejectedGroups = groups.filter(g => !g.linked)

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 720 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>キャラクター別名グループ — 同一人物の判定 ({candidates.length}件の候補)</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-search" style={{ fontSize: 12, color: '#6b7280' }}>
          領域ラベルで同じ箱に2つ以上の名前がついているアイテムから、名前の組み合わせを抽出しています。
          同一人物の別名(例: 本名/変身後の名前)なら「同一人物としてリンク」、たまたま2人が1つの箱に検出されただけなら「別人（リンクしない）」を選んでください。
        </div>

        <div style={{ padding: '8px 20px 20px', overflowY: 'auto', maxHeight: '70vh' }}>
          <h4 style={{ margin: '8px 0' }}>判定待ちの候補</h4>
          {loading && <div className="cgm-empty-hint">読み込み中…</div>}
          {!loading && candidates.length === 0 && (
            <div className="cgm-empty-hint">判定待ちの候補はありません 🎉</div>
          )}
          {candidates.map(c => {
            const key = c.characters.join('|')
            const busy = busyKey === key
            return (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid #f3f4f6' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{c.characters.join(' / ')}</div>
                  <div style={{ fontSize: 12, color: '#6b7280' }}>
                    {c.count}件のボックスで併記 · 例: {c.example_item_ids.map(id => `#${id}`).join(', ')}
                  </div>
                </div>
                <button className="btn" disabled={busy} onClick={() => decide(c.characters, true)}>同一人物としてリンク</button>
                <button className="btn" disabled={busy} onClick={() => decide(c.characters, false)}>別人（リンクしない）</button>
              </div>
            )
          })}

          <h4 style={{ margin: '20px 0 8px' }}>リンク済み ({linkedGroups.length}件)</h4>
          {linkedGroups.length === 0 && <div className="cgm-empty-hint">まだありません</div>}
          {linkedGroups.map(g => (
            <div key={g.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid #f3f4f6' }}>
              <div style={{ flex: 1 }}>{g.characters.join(' = ')}</div>
              <button className="btn" disabled={busyKey === `undo-${g.id}`} onClick={() => undo(g)}>取り消す</button>
            </div>
          ))}

          <h4 style={{ margin: '20px 0 8px' }}>別人と判定済み ({rejectedGroups.length}件)</h4>
          {rejectedGroups.length === 0 && <div className="cgm-empty-hint">まだありません</div>}
          {rejectedGroups.map(g => (
            <div key={g.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid #f3f4f6' }}>
              <div style={{ flex: 1, color: '#6b7280' }}>{g.characters.join(' / ')}</div>
              <button className="btn" disabled={busyKey === `undo-${g.id}`} onClick={() => undo(g)}>取り消す</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
