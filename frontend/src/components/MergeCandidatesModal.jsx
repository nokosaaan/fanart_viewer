import React, { useState, useEffect, useCallback } from 'react'
import ItemMergeModal from './ItemMergeModal'
import { getPlatformIcon } from '../lib/platformIcon'

function thumbUrl(it) {
  if (it.first_preview_id != null) {
    return `/api/items/${it.id}/preview/?id=${it.first_preview_id}`
  }
  return `/api/items/${it.id}/preview/`
}

export default function MergeCandidatesModal({ onClose }) {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [mergingGroup, setMergingGroup] = useState(null)

  const loadCandidates = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await fetch('/api/items/merge_candidates/', { cache: 'no-store' })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.detail || `候補の取得に失敗しました (${resp.status})`)
      setGroups(Array.isArray(data.groups) ? data.groups : [])
    } catch (e) {
      setError(e.message || '候補の取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadCandidates()
  }, [loadCandidates])

  return (
    <div className="cgm-panel-backdrop" onClick={onClose} onWheel={e => e.stopPropagation()}>
      <div
        className="cgm-panel"
        style={{ width: 960, maxWidth: '96vw', height: '86vh', maxHeight: '88vh' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="cgm-panel-header">
          <div>
            <strong>重複・マージ候補 ({groups.length}グループ)</strong>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
              タグリストが完全に同一のアイテム同士を重複候補として表示しています
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button type="button" className="btn" style={{ fontSize: 12 }} onClick={loadCandidates} disabled={loading}>
              {loading ? '検索中…' : '再スキャン'}
            </button>
            <button type="button" className="cgm-panel-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="cgm-panel-body" style={{ padding: '14px 16px', gap: 14 }}>
          {error && (
            <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>
          )}
          {loading ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>マージ候補を検索しています…</div>
          ) : groups.length === 0 ? (
            <div className="cgm-empty-hint" style={{ padding: 20 }}>
              タグリストが完全に一致する重複候補は見つかりませんでした 🎉
            </div>
          ) : (
            groups.map(group => (
              <div
                key={group.group_id}
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
                      fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999,
                      background: group.same_artist ? 'rgba(59,130,246,0.18)' : 'var(--hover-bg)',
                      color: group.same_artist ? 'var(--active-text)' : 'var(--text)',
                    }}>
                      {group.reason}
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                      {group.items.length}件 · タグ {group.tag_count}個
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    style={{ background: '#2563eb', color: '#fff', fontSize: 12, padding: '5px 12px', fontWeight: 600 }}
                    onClick={() => setMergingGroup(group)}
                  >
                    このグループをマージ…
                  </button>
                </div>

                <div style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  共通タグ: {group.tags.join(', ')}
                </div>

                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {group.items.map(it => {
                    const platform = getPlatformIcon(it.link)
                    return (
                      <div
                        key={it.id}
                        style={{
                          flex: '1 1 240px', maxWidth: 320, display: 'flex', gap: 10, alignItems: 'center',
                          padding: 8, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--card)',
                        }}
                      >
                        <a href={thumbUrl(it)} target="_blank" rel="noreferrer" title="画像を新しいタブで開く" style={{ flexShrink: 0, lineHeight: 0 }}>
                          <img
                            src={thumbUrl(it)}
                            alt=""
                            style={{ width: 60, height: 60, objectFit: 'cover', borderRadius: 4, background: '#1e293b' }}
                          />
                        </a>
                        <div style={{ flex: 1, minWidth: 0, fontSize: 12 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <strong style={{ fontSize: 13 }}>#{it.id}</strong>
                            <span style={{ color: 'var(--muted)', fontSize: 11 }}>{it.preview_count}枚</span>
                            {it.link && (
                              <a href={it.link} target="_blank" rel="noreferrer" title="元リンクを開く" style={{ display: 'inline-flex', alignItems: 'center' }}>
                                {platform ? (
                                  <img src={platform.icon} alt={platform.label} style={{ width: 14, height: 14, borderRadius: 2 }} />
                                ) : (
                                  <span style={{ fontSize: 11 }}>🔗</span>
                                )}
                              </a>
                            )}
                          </div>
                          <div style={{ color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 2 }}>
                            {it.artist || <span style={{ color: 'var(--text-faint)' }}>作者なし</span>}
                          </div>
                          <div style={{ color: 'var(--muted)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {(it.titles || []).join(', ') || 'タイトルなし'} / {(it.characters || []).join(', ') || 'キャラなし'}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {mergingGroup && (
        <ItemMergeModal
          items={mergingGroup.items}
          onClose={() => setMergingGroup(null)}
          onMerged={() => {
            setGroups(prev => prev.filter(g => g.group_id !== mergingGroup.group_id))
            setMergingGroup(null)
          }}
        />
      )}
    </div>
  )
}
