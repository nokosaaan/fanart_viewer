import React, { useState, useEffect, useCallback, useMemo } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

function classify(link) {
  if (!link.attempted) return 'unattempted'
  // A row a tag collision demoted (danbooru_tag cleared) whose actual fix
  // was renaming/merging this title's own name elsewhere has no tag to
  // link, by design — conflict_resolved is the human's explicit "I've
  // handled this" override, so it counts as linked regardless (see
  // TitleDanbooruLink.conflict_resolved).
  if (link.conflict_resolved) return 'linked'
  if (!link.danbooru_tag) return 'unresolved'
  return 'linked'
}

const TABS = [
  { key: 'all', label: 'すべて' },
  { key: 'unattempted', label: '未着手' },
  { key: 'unresolved', label: '未解決' },
  { key: 'linked', label: 'リンク済み' },
]

const RESOLVED_VIA_LABELS = {
  autocomplete: '自動解決(Danbooru検索)',
  human_review: '人手で確認済み',
  '': '未解決',
}

// Danbooru's own tag-category color convention (general/artist/copyright/
// character/meta) — same palette CharacterDanbooruLinkManager.jsx uses,
// kept in sync manually (see that file's own comment on this constant).
const CATEGORY_COLORS = { 0: '#60a5fa', 1: '#f87171', 3: '#c084fc', 4: '#4ade80', 5: '#facc15' }
const DEBOUNCE_MS = 250

// Title-side counterpart to CharacterDanbooruLinkManager.jsx — same
// review workflow, just for this app's own title vocabulary (Item.titles/
// CharacterGroup.titles) against Danbooru's copyright-category tags
// instead of character names against character-category tags. See
// item.views.TitleDanbooruLinkViewSet / item.danbooru_lookup.
// resolve_title_link.
export default function TitleDanbooruLinkManager({ onClose }) {
  const [links, setLinks] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('all')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(null)   // title_name whose debug panel is open
  const [resolving, setResolving] = useState(null) // title_name currently mid-resolve
  const [markingResolved, setMarkingResolved] = useState(null) // title_name currently mid mark_conflict_resolved
  const [manualFor, setManualFor] = useState(null) // title_name showing the manual-tag input
  const [manualValue, setManualValue] = useState('')
  const [error, setError] = useState('')
  // Live Danbooru tag-search suggestions for whichever row's manual-entry
  // box is open (see danbooru_lookup.autocomplete_tags) — same live
  // search-as-you-type UX as the character link manager.
  const [manualSuggestions, setManualSuggestions] = useState([])
  const [manualSuggestLoading, setManualSuggestLoading] = useState(false)

  useEffect(() => {
    if (!manualFor) { setManualSuggestions([]); return }
    const q = manualValue.trim()
    if (!q) { setManualSuggestions([]); setManualSuggestLoading(false); return }
    let cancelled = false
    setManualSuggestLoading(true)
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`/api/title-links/autocomplete/?q=${encodeURIComponent(q)}`)
        const j = await r.json().catch(() => [])
        if (!cancelled) setManualSuggestions(Array.isArray(j) ? j : [])
      } catch (_) {
        if (!cancelled) setManualSuggestions([])
      } finally {
        if (!cancelled) setManualSuggestLoading(false)
      }
    }, DEBOUNCE_MS)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [manualFor, manualValue])

  // Separate from manualSuggestions above: autocomplete_tags only ever
  // matches a tag's OWN romanized name — this instead searches Danbooru
  // wiki pages' other_names field (see danbooru_lookup.search_aliases),
  // for a title whose Danbooru tag name doesn't obviously match its
  // Japanese/English display name.
  const [aliasSuggestions, setAliasSuggestions] = useState([])
  const [aliasSuggestLoading, setAliasSuggestLoading] = useState(false)

  useEffect(() => {
    if (!manualFor) { setAliasSuggestions([]); return }
    const q = manualValue.trim()
    if (!q) { setAliasSuggestions([]); setAliasSuggestLoading(false); return }
    let cancelled = false
    setAliasSuggestLoading(true)
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`/api/title-links/alias_search/?q=${encodeURIComponent(q)}`)
        const j = await r.json().catch(() => [])
        if (!cancelled) setAliasSuggestions(Array.isArray(j) ? j : [])
      } catch (_) {
        if (!cancelled) setAliasSuggestions([])
      } finally {
        if (!cancelled) setAliasSuggestLoading(false)
      }
    }, DEBOUNCE_MS)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [manualFor, manualValue])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/title-links/')
      const data = await r.json().catch(() => [])
      setLinks(Array.isArray(data) ? data : [])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const counts = useMemo(() => {
    const c = { all: links.length, unattempted: 0, unresolved: 0, linked: 0 }
    links.forEach(l => { c[classify(l)]++ })
    return c
  }, [links])

  const q = query.trim().toLowerCase()
  const visible = links.filter(l => {
    if (tab !== 'all' && classify(l) !== tab) return false
    if (q && !l.title_name.toLowerCase().includes(q)) return false
    return true
  })

  function applyResult(result) {
    setLinks(prev => prev.map(l => {
      if (l.title_name === result.title_name) {
        return { ...l, attempted: true, danbooru_tag: result.danbooru_tag, resolved_via: result.resolved_via, match_score: result.match_score, debug_info: result.debug_info }
      }
      // A collision demotion can revert some OTHER title's link at the
      // same time (see dedupe_title_tag_collisions) — reflect that too.
      const demoted = (result.demotions || []).some(d => d.demoted.includes(l.title_name))
      if (demoted) return { ...l, danbooru_tag: null, resolved_via: '' }
      return l
    }))
  }

  async function resolveOne(name, { confirmOverwrite = false } = {}) {
    const existing = links.find(l => l.title_name === name)
    if (confirmOverwrite && existing?.resolved_via === 'human_review') {
      if (!window.confirm(`「${name}」は既に人手で確認済みです。自動解決で上書きしますか？`)) return
    }
    setResolving(name)
    setError('')
    try {
      const resp = await fetch('/api/title-links/resolve/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ title_name: name }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) { setError(j.detail || '解決に失敗しました'); return }
      applyResult(j)
      if (j.demotions && j.demotions.length > 0) {
        alert('タグの衝突を検出し、他のタイトルのリンクが解除されました:\n' +
          j.demotions.map(d => `${d.tag} -> ${d.winner} を優先、${d.demoted.join('、')} を解除`).join('\n'))
      }
    } catch (e) {
      setError('解決に失敗しました: ' + (e && e.message ? e.message : String(e)))
    } finally {
      setResolving(null)
    }
  }

  async function markConflictResolved(name, resolved) {
    setMarkingResolved(name)
    setError('')
    try {
      const resp = await fetch('/api/title-links/mark_conflict_resolved/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ title_name: name, resolved }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) { setError(j.detail || '更新に失敗しました'); return }
      setLinks(prev => prev.map(l => l.title_name === name ? { ...l, conflict_resolved: j.conflict_resolved } : l))
    } catch (e) {
      setError('更新に失敗しました: ' + (e && e.message ? e.message : String(e)))
    } finally {
      setMarkingResolved(null)
    }
  }

  async function submitManual(name, tag) {
    setError('')
    try {
      const resp = await fetch('/api/title-links/manual/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ title_name: name, danbooru_tag: tag }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) { setError(j.detail || '保存に失敗しました'); return }
      applyResult(j)
      setManualFor(null)
      setManualValue('')
    } catch (e) {
      setError('保存に失敗しました: ' + (e && e.message ? e.message : String(e)))
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>タイトル ↔ Danbooru リンク管理</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-search">
          <input
            className="cgm-search-input"
            placeholder="タイトル名で検索"
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

        <div className="cgm-panel-body">
          {loading && <div className="cgm-empty-hint">読み込み中…</div>}
          {!loading && visible.length === 0 && (
            <div className="cgm-empty-hint">該当するタイトルがありません</div>
          )}
          {!loading && visible.map(l => {
            const isExpanded = expanded === l.title_name
            const isResolving = resolving === l.title_name
            const isManual = manualFor === l.title_name
            return (
              <div key={l.title_name} style={{ border: '1px solid #334155', borderRadius: 6, background: '#1e293b', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', flexWrap: 'wrap' }}>
                  {/* readOnly input (not a plain span) so the name can be
                      click-dragged to select/copy, matching the same
                      read-only-field behavior used elsewhere in the app. */}
                  <input
                    type="text" readOnly value={l.title_name}
                    style={{
                      fontSize: 13, color: '#f1f5f9', fontWeight: 600,
                      background: 'transparent', border: 'none', outline: 'none', padding: 0,
                      width: `${l.title_name.length + 2}ch`, cursor: 'text',
                    }}
                  />
                  {l.danbooru_tag && (
                    <span style={{ fontSize: 12, color: '#86efac' }}>→ {l.danbooru_tag}</span>
                  )}
                  {l.conflict_resolved && !l.danbooru_tag && (
                    <span title="タグの衝突でリンクが解除された後、タイトル名の統一(リネーム/マージ)で手動解決済みとしてマークされています"
                      style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: '#14532d', color: '#86efac' }}>
                      ✓ 手動解決済み(命名統一)
                    </span>
                  )}
                  <span style={{ fontSize: 11, color: '#64748b' }}>{RESOLVED_VIA_LABELS[l.resolved_via] || l.resolved_via}</span>

                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn" disabled={isResolving} onClick={() => resolveOne(l.title_name, { confirmOverwrite: true })}
                      style={{ fontSize: 11, background: '#334155', color: '#f1f5f9' }}>
                      {isResolving ? '解決中…' : '🔎 自動解決'}
                    </button>
                    <button className="btn" onClick={() => { setManualFor(isManual ? null : l.title_name); setManualValue(l.danbooru_tag || '') }}
                      style={{ fontSize: 11, background: '#334155', color: '#f1f5f9' }}>
                      ✎ 手動入力
                    </button>
                    {l.danbooru_tag && (
                      <button className="btn" onClick={() => submitManual(l.title_name, null)}
                        style={{ fontSize: 11, background: '#7f1d1d', color: '#fecaca' }}>
                        リンク解除
                      </button>
                    )}
                    {l.attempted && !l.danbooru_tag && (
                      <button
                        className="btn"
                        disabled={markingResolved === l.title_name}
                        onClick={() => markConflictResolved(l.title_name, !l.conflict_resolved)}
                        title={l.conflict_resolved
                          ? 'このタイトルを再び「未解決」扱いに戻します'
                          : 'タグの衝突後、タイトル名を統一(リネーム/マージ)して解決済みであることを手動でマークします — Danbooruタグは付きません'}
                        style={{ fontSize: 11, background: l.conflict_resolved ? '#334155' : '#14532d', color: l.conflict_resolved ? '#f1f5f9' : '#86efac' }}
                      >
                        {markingResolved === l.title_name ? '更新中…' : (l.conflict_resolved ? '解決済みを解除' : '✓ 解決済みにする')}
                      </button>
                    )}
                    {l.debug_info && (
                      <button className="btn" onClick={() => setExpanded(isExpanded ? null : l.title_name)}
                        style={{ fontSize: 11, background: 'transparent', color: '#60a5fa' }}>
                        {isExpanded ? '根拠を閉じる ▲' : '根拠を見る ▼'}
                      </button>
                    )}
                  </div>
                </div>

                {isManual && (
                  <div style={{ padding: '0 10px 8px' }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <input
                        value={manualValue}
                        onChange={e => setManualValue(e.target.value)}
                        placeholder="タイトル名で検索(Danbooruの候補から選べます)"
                        style={{ fontSize: 12, padding: '4px 8px', flex: 1, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 4 }}
                        onKeyDown={e => { if (e.key === 'Enter' && manualValue.trim()) submitManual(l.title_name, manualValue.trim()) }}
                        autoFocus
                      />
                      <button className="btn" onClick={() => submitManual(l.title_name, manualValue.trim())} style={{ fontSize: 11 }}>保存</button>
                      <button className="btn" onClick={() => { setManualFor(null); setManualValue(''); setManualSuggestions([]); setAliasSuggestions([]) }} style={{ fontSize: 11 }}>キャンセル</button>
                    </div>
                    {manualSuggestLoading && (
                      <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>候補を検索中…</div>
                    )}
                    {!manualSuggestLoading && manualSuggestions.length > 0 && (
                      <div style={{ marginTop: 6, background: '#0f172a', border: '1px solid #334155', borderRadius: 6, maxHeight: 220, overflowY: 'auto' }}>
                        {manualSuggestions.map(s => (
                          <button
                            key={s.value}
                            className="dblink-suggestion"
                            onClick={() => submitManual(l.title_name, s.value)}
                            title={`このタグをリンクとして保存: ${s.value}`}
                          >
                            <span style={{ color: CATEGORY_COLORS[s.category] || '#e2e8f0', flex: 1 }}>{s.label}</span>
                            {s.post_count != null && <span style={{ fontSize: 11, color: '#64748b' }}>{s.post_count}</span>}
                          </button>
                        ))}
                      </div>
                    )}
                    {!manualSuggestLoading && manualSuggestions.length === 0 && !aliasSuggestLoading && aliasSuggestions.length > 0 && (
                      <div style={{ fontSize: 11, color: '#64748b', marginTop: 8, marginBottom: 2 }}>
                        見つかりませんでした。Danbooruの別表記(other_names)から見つかった候補:
                      </div>
                    )}
                    {!aliasSuggestLoading && aliasSuggestions.length > 0 && (
                      <div style={{ marginTop: 6, background: '#0f172a', border: '1px solid #334155', borderRadius: 6, maxHeight: 220, overflowY: 'auto' }}>
                        {aliasSuggestions.map(s => (
                          <button
                            key={s.tag}
                            className="dblink-suggestion"
                            onClick={() => submitManual(l.title_name, s.tag)}
                            title={`このタグをリンクとして保存: ${s.tag}`}
                            style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}
                          >
                            <span style={{ color: '#e2e8f0' }}>{s.tag}</span>
                            {s.other_names.length > 0 && (
                              <span style={{ fontSize: 11, color: '#64748b' }}>別表記: {s.other_names.join('、')}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {isExpanded && (
                  <div style={{ padding: '0 10px 8px', fontSize: 11, color: '#cbd5e1' }}>
                    {Array.isArray(l.debug_info?.candidates) ? (
                      l.debug_info.candidates.length > 0 ? (
                        <div>
                          {l.debug_info.candidates.map((c, i) => (
                            <span key={i} style={{ marginRight: 8, color: CATEGORY_COLORS[c.category] || '#e2e8f0' }}>
                              {c.label} {c.post_count != null && `(${c.post_count})`}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div>Danbooruに候補が見つかりませんでした</div>
                      )
                    ) : (
                      <div>{l.debug_info.reason || l.debug_info.demoted_reason || JSON.stringify(l.debug_info)}</div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
