import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { getPlatformIcon } from '../lib/platformIcon'

const IGNORED_PAIRS_KEY = 'fv_ignored_title_merge_pairs_v1'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

function loadIgnored() {
  try {
    const raw = localStorage.getItem(IGNORED_PAIRS_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(arr) ? arr : [])
  } catch (_) {
    return new Set()
  }
}

function saveIgnored(setObj) {
  try {
    localStorage.setItem(IGNORED_PAIRS_KEY, JSON.stringify([...setObj]))
  } catch (_) {}
}

function thumbUrl(it) {
  if (it.first_preview_id != null) {
    return `/api/items/${it.id}/preview/?id=${it.first_preview_id}`
  }
  return `/api/items/${it.id}/preview/`
}

export default function TitleMergeCandidatesModal({ onClose, onMerged = null }) {
  const [candidates, setCandidates] = useState([])
  const [minOverlap, setMinOverlap] = useState(3)
  const [filterText, setFilterText] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyPairId, setBusyPairId] = useState(null)
  const [error, setError] = useState('')
  const [infoMsg, setInfoMsg] = useState('')
  const [ignored, setIgnored] = useState(() => loadIgnored())

  const loadCandidates = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await fetch(`/api/items/title_merge_candidates/?min_overlap=${minOverlap}`, { cache: 'no-store' })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.detail || `候補の取得に失敗しました (${resp.status})`)
      setCandidates(Array.isArray(data.candidates) ? data.candidates : [])
    } catch (e) {
      setError(e.message || '候補の取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [minOverlap])

  useEffect(() => {
    loadCandidates()
  }, [loadCandidates])

  const visibleCandidates = useMemo(() => {
    const q = filterText.trim().toLowerCase()
    return candidates.filter(c => {
      if (ignored.has(c.pair_id)) return false
      if (!q) return true
      return (
        (c.title_a || '').toLowerCase().includes(q) ||
        (c.title_b || '').toLowerCase().includes(q)
      )
    })
  }, [candidates, ignored, filterText])

  async function handleMerge(c, sourceTitle, targetTitle) {
    if (!window.confirm(
      `タイトル「${sourceTitle}」を「${targetTitle}」に統一しますか？\n` +
      `（「${sourceTitle}」が付いている全アイテム・キャラグループが更新され、重複は整理されます）`
    )) return

    setBusyPairId(c.pair_id)
    setError('')
    setInfoMsg('')
    try {
      const resp = await fetch('/api/items/merge_titles/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
        },
        credentials: 'same-origin',
        body: JSON.stringify({
          source_title: sourceTitle,
          target_title: targetTitle,
        }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.detail || `統一に失敗しました (${resp.status})`)
      setInfoMsg(
        `「${sourceTitle}」→「${targetTitle}」に統一しました（アイテム ${data.updated_items || 0}件、グループ ${data.updated_groups || 0}件更新）`
      )
      if (onMerged) onMerged(data)
      await loadCandidates()
    } catch (e) {
      setError(e.message || '統一に失敗しました')
    } finally {
      setBusyPairId(null)
    }
  }

  function ignorePair(c) {
    setIgnored(prev => {
      const next = new Set(prev)
      next.add(c.pair_id)
      saveIgnored(next)
      return next
    })
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose} onWheel={e => e.stopPropagation()}>
      <div
        className="cgm-panel"
        style={{ width: 980, maxWidth: '96vw', height: '86vh', maxHeight: '88vh' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="cgm-panel-header">
          <div>
            <strong>タイトル表記ゆれ・統一候補 ({visibleCandidates.length}ペア)</strong>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
              同一アイテムに複数タイトルが重複して付いている組（{minOverlap}件以上）を検出しています
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button type="button" className="btn" style={{ fontSize: 12 }} onClick={loadCandidates} disabled={loading}>
              {loading ? '検索中…' : '再スキャン'}
            </button>
            <button type="button" className="cgm-panel-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="cgm-panel-search" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="タイトル名で絞り込み…"
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            style={{ flex: '1 1 220px', minWidth: 180 }}
          />
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, color: 'var(--muted)' }}>
            最小重複件数:
            <select value={minOverlap} onChange={e => setMinOverlap(Number(e.target.value))} style={{ fontSize: 12 }}>
              <option value={2}>2件以上</option>
              <option value={3}>3件以上（標準）</option>
              <option value={5}>5件以上</option>
              <option value={10}>10件以上</option>
            </select>
          </label>
          {ignored.size > 0 && (
            <button
              type="button"
              className="btn"
              style={{ fontSize: 11 }}
              onClick={() => { setIgnored(new Set()); saveIgnored(new Set()) }}
            >
              除外済みを再表示 ({ignored.size}件)
            </button>
          )}
        </div>

        {error && (
          <div style={{ padding: '8px 16px', background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: 13 }}>
            {error}
          </div>
        )}
        {infoMsg && (
          <div style={{ padding: '8px 16px', background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', fontSize: 13 }}>
            {infoMsg}
          </div>
        )}

        <div className="cgm-panel-body" style={{ padding: '14px 16px', gap: 14 }}>
          {loading ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>タイトルの重複候補を検索しています…</div>
          ) : visibleCandidates.length === 0 ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>
              {minOverlap}件以上重複しているタイトルペアは見つかりませんでした 🎉
            </div>
          ) : (
            visibleCandidates.map(c => {
              const isBusy = busyPairId === c.pair_id
              return (
                <div
                  key={c.pair_id}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: '12px 14px',
                    background: 'var(--bg)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 10,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{
                        fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 999,
                        background: 'rgba(245,158,11,0.18)', color: '#fcd34d',
                      }}>
                        同時付与 {c.co_count}件
                      </span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                        {c.title_a}
                        <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)', marginLeft: 4 }}>
                          (計{c.count_a} / 単独{c.only_a_count})
                        </span>
                      </span>
                      <span style={{ color: 'var(--muted)' }}>⇄</span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                        {c.title_b}
                        <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)', marginLeft: 4 }}>
                          (計{c.count_b} / 単独{c.only_b_count})
                        </span>
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        className="btn"
                        disabled={isBusy}
                        style={{ background: '#2563eb', color: '#fff', fontSize: 12, padding: '5px 10px', fontWeight: 600 }}
                        onClick={() => handleMerge(c, c.title_b, c.title_a)}
                        title={`「${c.title_b}」をすべて「${c.title_a}」に置き換えます`}
                      >
                        「{c.title_a}」に統一
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={isBusy}
                        style={{ background: '#059669', color: '#fff', fontSize: 12, padding: '5px 10px', fontWeight: 600 }}
                        onClick={() => handleMerge(c, c.title_a, c.title_b)}
                        title={`「${c.title_a}」をすべて「${c.title_b}」に置き換えます`}
                      >
                        「{c.title_b}」に統一
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={isBusy}
                        style={{ background: '#334155', color: '#cbd5e1', fontSize: 11, padding: '5px 8px' }}
                        onClick={() => ignorePair(c)}
                        title="別作品・コラボなど表記ゆれではないため非表示にします"
                      >
                        対象外
                      </button>
                    </div>
                  </div>

                  {c.sample_items && c.sample_items.length > 0 && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {c.sample_items.map(it => {
                        const platform = getPlatformIcon(it.link)
                        const previewSrc = thumbUrl(it)
                        const viewerHref = `/?image_viewer=1&src=${encodeURIComponent(previewSrc)}`
                        return (
                          <div
                            key={it.id}
                            style={{
                              flex: '1 1 200px', maxWidth: 240, display: 'flex', gap: 8, alignItems: 'center',
                              padding: 6, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--card)',
                            }}
                          >
                            <a href={viewerHref} target="_blank" rel="noreferrer" title="画像を新しいタブで開く" style={{ flexShrink: 0, lineHeight: 0 }}>
                              <img
                                src={previewSrc}
                                alt=""
                                loading="lazy"
                                style={{ width: 46, height: 46, objectFit: 'cover', borderRadius: 4, background: '#1e293b' }}
                              />
                            </a>
                            <div style={{ flex: 1, minWidth: 0, fontSize: 11 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                <strong>#{it.id}</strong>
                                {it.link && (
                                  <a href={it.link} target="_blank" rel="noreferrer" title="元リンクを開く" style={{ display: 'inline-flex', alignItems: 'center' }}>
                                    {platform ? (
                                      <img src={platform.icon} alt={platform.label} style={{ width: 12, height: 12, borderRadius: 2 }} />
                                    ) : (
                                      <span>🔗</span>
                                    )}
                                  </a>
                                )}
                              </div>
                              <div style={{ color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {it.artist || '作者なし'}
                              </div>
                              <div style={{ color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {(it.characters || []).join(', ') || 'キャラなし'}
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
