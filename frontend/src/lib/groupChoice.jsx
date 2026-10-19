import React, { useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const SKIP = 'skip'

// "Which group should these new characters go in?" -- asked when an item has
// several titles that lead to different groups, so the server could not know
// (see item/character_groups.py: it asks instead of guessing). `choice` is the
// `character_group_choice` a save response carries:
//   {characters: [...], options: [{title, group_id|null, group_name, create, suggested}, ...]}
// Resolves once the person has answered (or deferred), with the server's
// summary, or null if nothing was filed. Rendered into its own root so the
// three places that save (edit form, edit queue, region annotator) share one
// dialog without each owning its state.
export function ChoiceDialog({ itemId, choice, onDone }) {
  const defaultIndex = Math.max(0, choice.options.findIndex(o => o.suggested))
  const [picked, setPicked] = useState(() => Object.fromEntries(choice.characters.map(c => [c, defaultIndex])))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape' && !saving) onDone(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [saving, onDone])

  const label = o => o.create
    ? `新しいグループ「${o.group_name}」を作成して入れる`
    : `グループ「${o.group_name}」(タイトル: ${o.title})`

  function setAll(value) {
    setPicked(Object.fromEntries(choice.characters.map(c => [c, value === SKIP ? SKIP : Number(value)])))
  }

  async function submit() {
    const assignments = choice.characters
      .filter(c => picked[c] !== SKIP)
      .map(c => {
        const o = choice.options[picked[c]]
        return o.create ? { character: c, create_for_title: o.title } : { character: c, group_id: o.group_id }
      })
    if (assignments.length === 0) { onDone(null); return }
    setSaving(true)
    setError('')
    try {
      const resp = await fetch(`/api/items/${itemId}/assign_character_groups/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify({ assignments }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) { setError(j.detail || `配属に失敗しました (${resp.status})`); setSaving(false); return }
      onDone(j)
    } catch (e) {
      setError('配属に失敗しました: ' + (e && e.message ? e.message : String(e)))
      setSaving(false)
    }
  }

  const box = {
    background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 8,
    padding: '16px 18px', width: 'min(560px, 92vw)', maxHeight: '86vh', overflowY: 'auto',
  }
  const select = {
    background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 4,
    padding: '5px 8px', fontSize: 13, maxWidth: '100%',
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 100000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={box} role="dialog" aria-modal="true">
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>新しいキャラクターの配属先を選んでください</div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 12 }}>
          このアイテムには複数のタイトルがあり、キャラクターの入れ先のグループが複数考えられるため、自動では決めていません。
          「未分類のまま」にすると、あとで「キャラクターグループ」画面から手動で入れられます。
        </div>

        {choice.characters.length > 1 && (
          <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>全員をまとめて:</span>
            <select style={select} defaultValue="" onChange={e => { if (e.target.value !== '') setAll(e.target.value) }}>
              <option value="">(選んで一括設定)</option>
              {choice.options.map((o, i) => <option key={i} value={i}>{label(o)}</option>)}
              <option value={SKIP}>未分類のまま</option>
            </select>
          </div>
        )}

        {choice.characters.map(c => (
          <div key={c} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '6px 0', borderTop: '1px solid #334155' }}>
            <strong style={{ fontSize: 13, minWidth: 120 }}>{c}</strong>
            <select style={{ ...select, flex: 1, minWidth: 220 }} value={picked[c]} disabled={saving}
              onChange={e => setPicked(p => ({ ...p, [c]: e.target.value === SKIP ? SKIP : Number(e.target.value) }))}>
              {choice.options.map((o, i) => (
                <option key={i} value={i}>{label(o)}{o.suggested ? '(既定)' : ''}</option>
              ))}
              <option value={SKIP}>未分類のまま</option>
            </select>
          </div>
        ))}

        {error && <div style={{ marginTop: 10, fontSize: 12, color: '#f87171' }}>{error}</div>}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button className="btn" disabled={saving} onClick={() => onDone(null)}
            style={{ background: '#334155', color: '#f1f5f9' }}>あとで決める(未分類のまま)</button>
          <button className="btn" disabled={saving} onClick={submit}
            style={{ background: '#2563eb', color: '#fff', fontWeight: 600 }}>{saving ? '配属中…' : '決定'}</button>
        </div>
      </div>
    </div>
  )
}

export function askCharacterGroupChoice(itemId, choice) {
  return new Promise(resolve => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const finish = result => {
      root.unmount()
      host.remove()
      resolve(result)
    }
    root.render(<ChoiceDialog itemId={itemId} choice={choice} onDone={finish} />)
  })
}

// Everything a save response can say about filing characters into groups, in
// one place: the two automatic-filing notices, and -- when the server could
// not decide -- the question. Await it before moving on, so the dialog is
// answered before the save flow carries on (and a following save, e.g. the
// region save after the form save, doesn't stack a second dialog on top).
export async function handleGroupingResponse(itemId, resp) {
  if (!resp) return
  if (resp.auto_created_character_group) {
    alert(`新しいタイトル「${resp.auto_created_character_group.name}」のキャラクターグループを自動作成しました。`)
  }
  if (resp.auto_assigned_to_character_group) {
    const g = resp.auto_assigned_to_character_group
    alert(`新しいキャラクター(${(g.characters || []).join('、')})をキャラクターグループ「${g.name}」に自動的に割り当てました。`)
  }
  if (resp.character_group_choice && resp.character_group_choice.characters.length > 0) {
    const result = await askCharacterGroupChoice(itemId, resp.character_group_choice)
    if (result && result.filed && result.filed.length > 0) {
      alert(result.filed.map(f => `${f.characters.join('、')} → グループ「${f.group_name}」`).join('\n') + '\nに割り当てました。')
    }
  }
}
