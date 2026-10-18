import React, { useState, useEffect, useCallback, useMemo } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Status of a title -> label/colour/explanation. Keep in step with
// item/title_group_views.py's module docstring.
const STATUS = {
  linked: { label: '紐づけ済み', bg: '#14532d', fg: '#86efac', hint: 'グループが1つだけこのタイトルを持っています' },
  multi: { label: '複数(親子)', bg: '#1e3a8a', fg: '#bfdbfe', hint: '複数のグループがこのタイトルを持ちますが、最も具体的(階層が深い)グループが配属先です' },
  ambiguous: { label: '要確認(同格が複数)', bg: '#78350f', fg: '#fde68a', hint: '同じ階層のグループが複数このタイトルを持っています。どれが正しいか決めて、不要な方の紐づけを外してください' },
  legacy: { label: '旧形式(名前一致のみ)', bg: '#4c1d95', fg: '#ddd6fe', hint: 'タイトルは未登録ですが、同名のグループがあります。「紐づける」でタイトルを登録できます' },
  unlinked: { label: 'グループなし', bg: '#7f1d1d', fg: '#fecaca', hint: 'このタイトルのグループがありません' },
}

const TABS = [
  { key: 'all', label: 'すべて' },
  { key: 'needs', label: '要対応' },
  { key: 'ungrouped', label: '未分類キャラあり' },
  { key: 'unlinked', label: 'グループなし' },
  { key: 'legacy', label: '旧形式' },
  { key: 'ambiguous', label: '要確認' },
  { key: 'linked', label: '紐づけ済み' },
  { key: 'groups', label: 'タイトル未設定のグループ' },
]

function needsAttention(t) {
  return t.status === 'unlinked' || t.status === 'legacy' || t.status === 'ambiguous' || t.ungrouped_characters.length > 0
}

// Human review of how titles and character groups are linked — the same
// idea as TitleDanbooruLinkManager.jsx, for item/title_group_views.py. New
// characters are filed into their title's group automatically
// (item/character_groups.py); that can only use links that exist, so this
// shows where a link is missing, doubtful, or never written.
export default function TitleGroupLinkManager({ onClose }) {
  const [data, setData] = useState({ titles: [], unlinked_groups: [], ungrouped_character_count: 0 })
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('needs')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/title-group-links/', { credentials: 'same-origin' })
      const j = await r.json().catch(() => null)
      if (j && Array.isArray(j.titles)) setData(j)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function post(action, body, key) {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      const r = await fetch(`/api/title-group-links/${action}/`, {
        method: 'POST', headers: HEADERS, credentials: 'same-origin', body: JSON.stringify(body),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) { setError(j.detail || '操作に失敗しました'); return null }
      await load()
      return j
    } catch (e) {
      setError('操作に失敗しました: ' + (e && e.message ? e.message : String(e)))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function link(title, group) {
    await post('link', { title_name: title, group_id: group.id }, `link:${title}:${group.id}`)
  }

  async function unlink(title, group) {
    if (!window.confirm(`グループ「${group.name}」から、タイトル「${title}」の紐づけを外しますか？\n(グループ内のキャラは変わりません)`)) return
    await post('unlink', { title_name: title, group_id: group.id }, `unlink:${title}:${group.id}`)
  }

  async function createGroup(title) {
    if (!window.confirm(`タイトル「${title}」と同名のグループを新規作成して紐づけますか？`)) return
    const j = await post('create_group', { title_name: title }, `create:${title}`)
    if (j) setNotice(j.created ? `グループ「${title}」を作成しました` : `既存のグループ「${title}」にタイトルを紐づけました`)
  }

  async function assign(title) {
    const j = await post('assign_characters', { title_name: title }, `assign:${title}`)
    if (j) {
      setNotice(j.assigned.length
        ? `${j.assigned.join('、')} を ${j.groups.map(g => `「${g}」`).join('、')} に配属しました`
        : '配属先のグループがないため、配属できるキャラはありませんでした(先にグループを紐づけてください)')
    }
  }

  const counts = useMemo(() => {
    const c = {
      all: data.titles.length, needs: 0, ungrouped: 0, unlinked: 0, legacy: 0, ambiguous: 0, linked: 0,
      groups: data.unlinked_groups.length,
    }
    data.titles.forEach(t => {
      if (needsAttention(t)) c.needs++
      if (t.ungrouped_characters.length > 0) c.ungrouped++
      if (t.status === 'unlinked') c.unlinked++
      if (t.status === 'legacy') c.legacy++
      if (t.status === 'ambiguous') c.ambiguous++
      if (t.status === 'linked' || t.status === 'multi') c.linked++
    })
    return c
  }, [data])

  const q = query.trim().toLowerCase()
  const visibleTitles = data.titles.filter(t => {
    if (q && !t.title_name.toLowerCase().includes(q)) return false
    switch (tab) {
      case 'needs': return needsAttention(t)
      case 'ungrouped': return t.ungrouped_characters.length > 0
      case 'unlinked': return t.status === 'unlinked'
      case 'legacy': return t.status === 'legacy'
      case 'ambiguous': return t.status === 'ambiguous'
      case 'linked': return t.status === 'linked' || t.status === 'multi'
      default: return true
    }
  })
  const visibleGroups = data.unlinked_groups.filter(g => !q || g.name.toLowerCase().includes(q))

  const chip = (extra = {}) => ({
    fontSize: 11, padding: '2px 8px', borderRadius: 10, background: '#334155', color: '#e2e8f0', ...extra,
  })

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>タイトル ↔ キャラグループ紐づけ管理</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div style={{ padding: '0 14px 8px', fontSize: 12, color: '#94a3b8' }}>
          新しいキャラは、タイトルに紐づくグループへ自動で入ります(既にグループにいるキャラは動きません)。
          紐づけが無い・曖昧なタイトルはここで確認して直せます。未分類のキャラ: <strong>{data.ungrouped_character_count}</strong> 人
        </div>

        <div className="cgm-panel-search">
          <input
            className="cgm-search-input"
            placeholder={tab === 'groups' ? 'グループ名で検索' : 'タイトル名で検索'}
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', gap: 6, padding: '0 14px 10px', flexWrap: 'wrap' }}>
          {TABS.map(t => (
            <button key={t.key} className="btn" onClick={() => setTab(t.key)}
              style={{ fontSize: 12, background: tab === t.key ? '#2563eb' : '#334155', color: '#f1f5f9' }}>
              {t.label} ({counts[t.key]})
            </button>
          ))}
        </div>

        {error && <div style={{ padding: '0 14px 10px', fontSize: 12, color: '#f87171' }}>{error}</div>}
        {notice && <div style={{ padding: '0 14px 10px', fontSize: 12, color: '#86efac' }}>{notice}</div>}

        <div className="cgm-panel-body">
          {loading && <div className="cgm-empty-hint">読み込み中…</div>}

          {!loading && tab !== 'groups' && visibleTitles.length === 0 && (
            <div className="cgm-empty-hint">該当するタイトルがありません</div>
          )}

          {!loading && tab !== 'groups' && visibleTitles.map(t => {
            const st = STATUS[t.status] || STATUS.unlinked
            return (
              <div key={t.title_name} style={{ border: '1px solid #334155', borderRadius: 6, background: '#1e293b', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', flexWrap: 'wrap' }}>
                  <input
                    type="text" readOnly value={t.title_name}
                    style={{
                      fontSize: 13, color: '#f1f5f9', fontWeight: 600, background: 'transparent', border: 'none',
                      outline: 'none', padding: 0, width: `${Math.min(t.title_name.length, 40) + 2}ch`, cursor: 'text',
                    }}
                  />
                  <span title={st.hint} style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: st.bg, color: st.fg }}>{st.label}</span>
                  <span style={{ fontSize: 11, color: '#64748b' }}>{t.item_count} 件</span>

                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {(t.status === 'unlinked') && (
                      <button className="btn" disabled={busy === `create:${t.title_name}`} onClick={() => createGroup(t.title_name)}
                        style={{ fontSize: 11, background: '#14532d', color: '#86efac' }}>
                        ＋ グループを新規作成
                      </button>
                    )}
                    {t.ungrouped_characters.length > 0 && t.claimants.length > 0 && (
                      <button className="btn" disabled={busy === `assign:${t.title_name}`} onClick={() => assign(t.title_name)}
                        title="このタイトルの未分類キャラを、配属先のグループへ入れます"
                        style={{ fontSize: 11, background: '#334155', color: '#f1f5f9' }}>
                        {busy === `assign:${t.title_name}` ? '配属中…' : `未分類${t.ungrouped_characters.length}人を配属`}
                      </button>
                    )}
                  </div>
                </div>

                {t.claimants.length > 0 && (
                  <div style={{ padding: '0 10px 8px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#64748b' }}>グループ:</span>
                    {t.claimants.map(g => (
                      <span key={g.id} style={chip({ background: g.id === t.target_group_id ? '#14532d' : '#334155', color: g.id === t.target_group_id ? '#bbf7d0' : '#e2e8f0' })}
                        title={g.id === t.target_group_id ? '新しいキャラはこのグループに配属されます' : ''}>
                        {g.parent_name ? `${g.parent_name} › ` : ''}{g.name}（{g.character_count}人）
                        {g.id === t.target_group_id && ' ★配属先'}
                        {g.via === 'name' && ' ・名前一致のみ'}
                        {g.via === 'titles' && (
                          <button onClick={() => unlink(t.title_name, g)} title="このグループからタイトルの紐づけを外す"
                            style={{ marginLeft: 6, background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', padding: 0 }}>✕</button>
                        )}
                        {g.via === 'name' && (
                          <button onClick={() => link(t.title_name, g)} title="タイトルをこのグループに登録する"
                            style={{ marginLeft: 6, background: 'none', border: 'none', color: '#93c5fd', cursor: 'pointer', padding: 0 }}>紐づける</button>
                        )}
                      </span>
                    ))}
                  </div>
                )}

                {t.suggestions.length > 0 && (
                  <div style={{ padding: '0 10px 8px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#64748b' }}>候補:</span>
                    {t.suggestions.map(g => (
                      <button key={g.id} className="btn" disabled={busy === `link:${t.title_name}:${g.id}`}
                        onClick={() => link(t.title_name, g)}
                        title={`このタイトルにあるキャラのうち ${g.overlap} 人がこのグループにいます${g.name_match ? '。名前も一致' : ''}`}
                        style={{ fontSize: 11, background: '#1e3a8a', color: '#bfdbfe' }}>
                        {g.parent_name ? `${g.parent_name} › ` : ''}{g.name}（共通{g.overlap}人{g.name_match ? '・名前一致' : ''}）に紐づける
                      </button>
                    ))}
                  </div>
                )}

                {t.ungrouped_characters.length > 0 && (
                  <div style={{ padding: '0 10px 8px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#fbbf24' }}>未分類:</span>
                    {t.ungrouped_characters.map(c => (
                      <span key={c.name} style={chip({ background: '#78350f', color: '#fde68a' })}>{c.name}</span>
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {!loading && tab === 'groups' && visibleGroups.length === 0 && (
            <div className="cgm-empty-hint">タイトルが未設定のグループはありません</div>
          )}
          {!loading && tab === 'groups' && (
            <div style={{ padding: '0 0 8px', fontSize: 11, color: '#64748b' }}>
              タイトルが1つも紐づいていないグループです。anime / game などの親グループは、紐づけなくて構いません。
            </div>
          )}
          {!loading && tab === 'groups' && visibleGroups.map(g => (
            <div key={g.id} style={{ border: '1px solid #334155', borderRadius: 6, background: '#1e293b', marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 13, color: '#f1f5f9' }}>{g.parent_name ? `${g.parent_name} › ` : ''}{g.name}</strong>
                <span style={{ fontSize: 11, color: '#64748b' }}>キャラ {g.character_count} 人</span>
                {g.child_count > 0 && <span style={{ fontSize: 11, color: '#64748b' }}>子グループ {g.child_count}</span>}
              </div>
              {g.suggested_titles.length > 0 ? (
                <div style={{ padding: '0 10px 8px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: '#64748b' }}>このグループのキャラが使われているタイトル:</span>
                  {g.suggested_titles.map(s => (
                    <button key={s.title_name} className="btn" disabled={busy === `link:${s.title_name}:${g.id}`}
                      onClick={() => link(s.title_name, g)}
                      title={`${s.items} 件のアイテムに使われているタイトル${s.name_match ? '(グループ名と一致)' : ''}`}
                      style={{ fontSize: 11, background: '#1e3a8a', color: '#bfdbfe' }}>
                      {s.title_name}（{s.items}件{s.name_match ? '・名前一致' : ''}）に紐づける
                    </button>
                  ))}
                </div>
              ) : (
                <div style={{ padding: '0 10px 8px', fontSize: 11, color: '#64748b' }}>候補になるタイトルが見つかりませんでした</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
