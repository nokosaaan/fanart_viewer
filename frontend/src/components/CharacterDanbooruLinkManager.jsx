import React, { useState, useEffect, useCallback, useMemo } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Same 0.6 floor views._match_tagger_characters uses to decide whether an
// automated CharacterDanbooruLink is trusted for auto-suggestion — kept in
// sync manually since this is a display-only threshold on the frontend,
// not something the API currently returns as a computed flag.
const TRUST_THRESHOLD = 0.6

function classify(link) {
  if (!link.attempted) return 'unattempted'
  if (!link.danbooru_tag) return 'unresolved'
  if (link.resolved_via === 'title_roster' && (link.match_score ?? 0) < TRUST_THRESHOLD) return 'low_confidence'
  return 'linked'
}

const TABS = [
  { key: 'all', label: 'すべて' },
  { key: 'unattempted', label: '未着手' },
  { key: 'unresolved', label: '未解決' },
  { key: 'low_confidence', label: '低確信度' },
  { key: 'linked', label: 'リンク済み' },
]

const RESOLVED_VIA_LABELS = {
  title_roster: '自動解決(タイトルロースター照合)',
  human_review: '人手で確認済み',
  '': '未解決',
}

// Frontend counterpart to item.management.commands.link_danbooru_characters
// and the one-off interactive review artifact used to bulk-review its
// first run (see item.views.CharacterDanbooruLinkViewSet) — makes the same
// "this app's character name <-> Danbooru's own tag" review workflow a
// permanent part of the app instead of a throwaway tool.
export default function CharacterDanbooruLinkManager({ onClose }) {
  const [links, setLinks] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('all')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(null)   // character_name whose debug panel is open
  const [resolving, setResolving] = useState(null) // character_name currently mid-resolve
  const [manualFor, setManualFor] = useState(null) // character_name showing the manual-tag input
  const [manualValue, setManualValue] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/character-links/')
      const data = await r.json().catch(() => [])
      setLinks(Array.isArray(data) ? data : [])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const counts = useMemo(() => {
    const c = { all: links.length, unattempted: 0, unresolved: 0, low_confidence: 0, linked: 0 }
    links.forEach(l => { c[classify(l)]++ })
    return c
  }, [links])

  const q = query.trim().toLowerCase()
  const visible = links.filter(l => {
    if (tab !== 'all' && classify(l) !== tab) return false
    if (q && !l.character_name.toLowerCase().includes(q) && !l.titles.some(t => t.toLowerCase().includes(q))) return false
    return true
  })

  function applyResult(result) {
    setLinks(prev => prev.map(l => {
      if (l.character_name === result.character_name) {
        return { ...l, attempted: true, danbooru_tag: result.danbooru_tag, resolved_via: result.resolved_via, match_score: result.match_score, debug_info: result.debug_info }
      }
      // A collision demotion can revert some OTHER character's link at the
      // same time (see dedupe_tag_collisions) — reflect that too instead
      // of leaving the list showing a tag two rows now both claim.
      const demoted = (result.demotions || []).some(d => d.demoted.includes(l.character_name))
      if (demoted) return { ...l, danbooru_tag: null, resolved_via: '' }
      return l
    }))
  }

  async function resolveOne(name, { confirmOverwrite = false } = {}) {
    const existing = links.find(l => l.character_name === name)
    if (confirmOverwrite && existing?.resolved_via === 'human_review') {
      if (!window.confirm(`「${name}」は既に人手で確認済みです。自動解決で上書きしますか？`)) return
    }
    setResolving(name)
    setError('')
    try {
      const resp = await fetch('/api/character-links/resolve/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ character_name: name }),
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) { setError(j.detail || '解決に失敗しました'); return }
      applyResult(j)
      if (j.demotions && j.demotions.length > 0) {
        alert('タグの衝突を検出し、他のキャラのリンクが解除されました:\n' +
          j.demotions.map(d => `${d.tag} -> ${d.winner} を優先、${d.demoted.join('、')} を解除`).join('\n'))
      }
    } catch (e) {
      setError('解決に失敗しました: ' + (e && e.message ? e.message : String(e)))
    } finally {
      setResolving(null)
    }
  }

  async function submitManual(name, tag) {
    setError('')
    try {
      const resp = await fetch('/api/character-links/manual/', {
        method: 'POST', headers: HEADERS, credentials: 'same-origin',
        body: JSON.stringify({ character_name: name, danbooru_tag: tag }),
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
          <strong>キャラ ↔ Danbooru リンク管理</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-search">
          <input
            className="cgm-search-input"
            placeholder="キャラクター名・タイトルで検索"
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
            <div className="cgm-empty-hint">該当するキャラクターがありません</div>
          )}
          {!loading && visible.map(l => {
            const isExpanded = expanded === l.character_name
            const isResolving = resolving === l.character_name
            const isManual = manualFor === l.character_name
            const cls = classify(l)
            return (
              <div key={l.character_name} style={{ border: '1px solid #334155', borderRadius: 6, background: '#1e293b', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 13, color: '#f1f5f9', fontWeight: 600 }}>{l.character_name}</span>
                  {l.danbooru_tag && (
                    <span style={{ fontSize: 12, color: '#86efac' }}>→ {l.danbooru_tag}</span>
                  )}
                  {cls === 'low_confidence' && (
                    <span title="自動解決の確信度がsuggest_tagsの信頼しきい値(0.6)未満です — 誤りの可能性があります"
                      style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: '#78350f', color: '#fde68a' }}>
                      ⚠ 低確信度
                    </span>
                  )}
                  {l.match_score != null && (
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>score {l.match_score.toFixed(2)}</span>
                  )}
                  <span style={{ fontSize: 11, color: '#64748b' }}>{RESOLVED_VIA_LABELS[l.resolved_via] || l.resolved_via}</span>

                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn" disabled={isResolving} onClick={() => resolveOne(l.character_name, { confirmOverwrite: true })}
                      style={{ fontSize: 11, background: '#334155', color: '#f1f5f9' }}>
                      {isResolving ? '解決中…' : '🔎 自動解決'}
                    </button>
                    <button className="btn" onClick={() => { setManualFor(isManual ? null : l.character_name); setManualValue(l.danbooru_tag || '') }}
                      style={{ fontSize: 11, background: '#334155', color: '#f1f5f9' }}>
                      ✎ 手動入力
                    </button>
                    {l.danbooru_tag && (
                      <button className="btn" onClick={() => submitManual(l.character_name, null)}
                        style={{ fontSize: 11, background: '#7f1d1d', color: '#fecaca' }}>
                        リンク解除
                      </button>
                    )}
                    {l.debug_info && (
                      <button className="btn" onClick={() => setExpanded(isExpanded ? null : l.character_name)}
                        style={{ fontSize: 11, background: 'transparent', color: '#60a5fa' }}>
                        {isExpanded ? '根拠を閉じる ▲' : '根拠を見る ▼'}
                      </button>
                    )}
                  </div>
                </div>

                {isManual && (
                  <div style={{ padding: '0 10px 8px', display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      value={manualValue}
                      onChange={e => setManualValue(e.target.value)}
                      placeholder="Danbooruのタグ名 (例: hakurei_reimu)"
                      style={{ fontSize: 12, padding: '4px 8px', flex: 1, background: '#0f172a', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 4 }}
                      onKeyDown={e => { if (e.key === 'Enter' && manualValue.trim()) submitManual(l.character_name, manualValue.trim()) }}
                      autoFocus
                    />
                    <button className="btn" onClick={() => submitManual(l.character_name, manualValue.trim())} style={{ fontSize: 11 }}>保存</button>
                    <button className="btn" onClick={() => { setManualFor(null); setManualValue('') }} style={{ fontSize: 11 }}>キャンセル</button>
                  </div>
                )}

                {isExpanded && (
                  <div style={{ padding: '0 10px 8px', fontSize: 11, color: '#cbd5e1' }}>
                    <div style={{ marginBottom: 4, color: '#94a3b8' }}>タイトル: {l.titles.join('、') || '（不明）'}</div>
                    {Array.isArray(l.debug_info) ? l.debug_info.map((entry, i) => (
                      <div key={i} style={{ padding: '3px 0', borderTop: i > 0 ? '1px solid #334155' : 'none' }}>
                        <span style={{ color: '#93c5fd' }}>{entry.title}</span>
                        {entry.wiki_tag ? <> — wiki: {entry.wiki_tag} (roster {entry.roster_size}件)</> : <> — wikiページ未検出</>}
                        {entry.top_scores && entry.top_scores.length > 0 && (
                          <div style={{ marginTop: 2 }}>
                            {entry.top_scores.map(([tag, score], j) => (
                              <span key={j} style={{ marginRight: 8 }}>{tag} ({score.toFixed(2)})</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )) : (
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
