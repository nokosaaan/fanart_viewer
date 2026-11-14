import React, { useState, useEffect, useMemo } from 'react'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'

function getCookie(name) {
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

function uniqueStrings(lists) {
  const seen = new Set()
  const out = []
  for (const list of lists) {
    for (const raw of (list || [])) {
      const s = String(raw || '').trim()
      if (s && !seen.has(s)) {
        seen.add(s)
        out.push(s)
      }
    }
  }
  return out
}

function previewThumbUrl(it) {
  if (it.first_preview_id != null) {
    return `/api/items/${it.id}/preview/?id=${it.first_preview_id}`
  }
  return `/api/items/${it.id}/preview/`
}

export default function ItemMergeModal({ items, onClose, onMerged }) {
  // Default target: item with the most images, breaking ties by highest id (newest)
  const defaultTargetId = useMemo(() => {
    if (!items || items.length === 0) return null
    const sorted = [...items].sort((a, b) => {
      const ca = a.preview_count || 0
      const cb = b.preview_count || 0
      if (cb !== ca) return cb - ca
      return b.id - a.id
    })
    return sorted[0].id
  }, [items])

  const [targetId, setTargetId] = useState(defaultTargetId)
  const [previewsByItem, setPreviewsByItem] = useState({})
  const [loadingPreviews, setLoadingPreviews] = useState(true)
  // Ordered list of kept image entries: { key, type: 'preview'|'legacy', id?, item_id, url, sourceItemId, sourceOrder }
  const [keptImages, setKeptImages] = useState([])

  // Editable metadata fields
  const [titlesText, setTitlesText] = useState('')
  const [charactersText, setCharactersText] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [situation, setSituation] = useState('')
  const [artist, setArtist] = useState('')
  const [link, setLink] = useState('')
  const [description, setDescription] = useState('')

  const [merging, setMerging] = useState(false)
  const [error, setError] = useState('')

  const targetItem = useMemo(() => items.find(it => it.id === targetId) || items[0], [items, targetId])
  const sourceItems = useMemo(() => items.filter(it => it.id !== targetId), [items, targetId])

  // Load previews for every participating item
  useEffect(() => {
    let cancelled = false
    setLoadingPreviews(true)
    Promise.all(
      items.map(async (it) => {
        try {
          const resp = await fetch(`/api/items/${it.id}/previews/`, { cache: 'no-store' })
          const data = await resp.json().catch(() => [])
          if (Array.isArray(data) && data.length > 0) {
            return [
              it.id,
              data.map((p, idx) => ({
                key: `preview-${p.id}`,
                type: 'preview',
                id: p.id,
                item_id: it.id,
                url: `/api/items/${it.id}/preview/?id=${p.id}`,
                sourceItemId: it.id,
                sourceOrder: idx + 1,
              })),
            ]
          }
        } catch (e) {
          console.error('Failed to load previews for item', it.id, e)
        }
        if ((it.preview_count || 0) > 0 || it.has_preview) {
          return [
            it.id,
            [{
              key: `legacy-${it.id}`,
              type: 'legacy',
              item_id: it.id,
              url: `/api/items/${it.id}/preview/`,
              sourceItemId: it.id,
              sourceOrder: 1,
            }],
          ]
        }
        return [it.id, []]
      })
    ).then((entries) => {
      if (cancelled) return
      const map = Object.fromEntries(entries)
      setPreviewsByItem(map)
      setLoadingPreviews(false)
    })
    return () => { cancelled = true }
  }, [items])

  // Initialize keptImages & metadata when targetId changes or previews finish loading
  useEffect(() => {
    if (loadingPreviews || !targetItem) return
    setKeptImages(previewsByItem[targetItem.id] || [])
  }, [targetItem, previewsByItem, loadingPreviews])

  useEffect(() => {
    if (!targetItem) return
    const ordered = [targetItem, ...sourceItems]
    setTitlesText(uniqueStrings(ordered.map(it => it.titles)).join(', '))
    setCharactersText(uniqueStrings(ordered.map(it => it.characters)).join(', '))
    setTagsText(uniqueStrings(ordered.map(it => it.tags)).join(', '))
    setSituation(targetItem.situation || sourceItems.find(s => s.situation)?.situation || '')
    setArtist(targetItem.artist || sourceItems.find(s => s.artist)?.artist || '')
    setLink(targetItem.link || sourceItems.find(s => s.link)?.link || '')
    setDescription(targetItem.description || sourceItems.find(s => s.description)?.description || '')
  }, [targetItem, sourceItems])

  const keptKeySet = useMemo(() => new Set(keptImages.map(img => img.key)), [keptImages])

  function addImageToEnd(img) {
    setKeptImages(prev => prev.some(p => p.key === img.key) ? prev : [...prev, img])
  }

  function removeKeptImage(key) {
    setKeptImages(prev => prev.filter(p => p.key !== key))
  }

  function moveKeptImage(idx, delta) {
    setKeptImages(prev => {
      const nextIdx = idx + delta
      if (nextIdx < 0 || nextIdx >= prev.length) return prev
      const copy = [...prev]
      const [item] = copy.splice(idx, 1)
      copy.splice(nextIdx, 0, item)
      return copy
    })
  }

  function parseCsv(text) {
    return String(text || '').split(',').map(s => s.trim()).filter(Boolean)
  }

  async function handleMerge() {
    if (!targetItem || sourceItems.length === 0 || merging) return
    if (keptImages.length === 0) {
      if (!window.confirm('統合後の画像が0枚になりますが、このままマージしますか？')) return
    }
    setMerging(true)
    setError('')
    try {
      const payload = {
        target_id: targetItem.id,
        source_ids: sourceItems.map(s => s.id),
        images: keptImages.map(img => (
          img.type === 'preview'
            ? { type: 'preview', id: img.id }
            : { type: 'legacy', item_id: img.item_id }
        )),
        titles: parseCsv(titlesText),
        characters: parseCsv(charactersText),
        tags: parseCsv(tagsText),
        situation: situation.trim().toUpperCase(),
        artist: artist.trim(),
        link: link.trim(),
        description,
      }
      const resp = await fetch('/api/items/merge/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.detail || `マージに失敗しました (${resp.status})`)

      if (data.item) {
        notify('item-updated', { id: data.item.id, item: data.item })
      }
      for (const sid of (data.deleted_ids || sourceItems.map(s => s.id))) {
        notify('item-deleted', { id: sid })
      }
      if (onMerged) onMerged(data)
    } catch (e) {
      setError(e.message || 'マージに失敗しました')
    } finally {
      setMerging(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 1600,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
      onWheel={e => e.stopPropagation()}
    >
      <div
        style={{
          width: 920, maxWidth: '96vw', maxHeight: '92vh', background: '#1e293b', color: '#f1f5f9',
          borderRadius: 12, display: 'flex', flexDirection: 'column', overflow: 'hidden',
          boxShadow: '0 12px 48px rgba(0,0,0,0.7)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '14px 20px', borderBottom: '1px solid #334155', flexShrink: 0,
        }}>
          <div>
            <strong style={{ fontSize: 16 }}>アイテムをマージ ({items.length}件 → 1件)</strong>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
              統合先のアイテムを残し、統合元のアイテムを削除して画像・メタデータをまとめます
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: 20, cursor: 'pointer' }}
          >✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: '1 1 auto', overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {error && (
            <div style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid #ef4444', color: '#fca5a5', padding: '8px 12px', borderRadius: 6, fontSize: 13 }}>
              {error}
            </div>
          )}

          {/* 1. Select Target Item */}
          <div style={{ background: '#0f172a', borderRadius: 8, padding: '12px 14px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
              1. 統合先（残すアイテム）を選択
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map(it => {
                const isTarget = it.id === targetId
                const platform = getPlatformIcon(it.link)
                return (
                  <label
                    key={it.id}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '8px 10px', borderRadius: 6,
                      cursor: 'pointer',
                      background: isTarget ? 'rgba(59,130,246,0.18)' : 'rgba(255,255,255,0.03)',
                      border: isTarget ? '1px solid #3b82f6' : '1px solid #334155',
                    }}
                  >
                    <input
                      type="radio"
                      name="merge-target"
                      checked={isTarget}
                      onChange={() => setTargetId(it.id)}
                    />
                    <img
                      src={previewThumbUrl(it)}
                      alt=""
                      style={{ width: 48, height: 48, objectFit: 'cover', borderRadius: 4, background: '#1e293b', flexShrink: 0 }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: 13 }}>#{it.id}</span>
                        <span style={{
                          fontSize: 11, padding: '1px 6px', borderRadius: 4,
                          background: isTarget ? '#2563eb' : '#475569', color: '#fff',
                        }}>
                          {isTarget ? '統合先（残す）' : '統合元（マージ後削除）'}
                        </span>
                        <span style={{ fontSize: 12, color: '#cbd5e1' }}>画像 {it.preview_count || 0}枚</span>
                        {it.artist && <span style={{ fontSize: 12, color: '#94a3b8' }}>{it.artist}</span>}
                        {platform && <img src={platform.icon} alt={platform.label} style={{ width: 14, height: 14, borderRadius: 2 }} />}
                      </div>
                      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {(it.titles || []).join(', ') || 'タイトルなし'} / {(it.characters || []).join(', ') || 'キャラなし'}
                        {it.link ? ` — ${it.link}` : ''}
                      </div>
                    </div>
                  </label>
                )
              })}
            </div>
          </div>

          {/* 2. Image Selection & Order */}
          <div style={{ background: '#0f172a', borderRadius: 8, padding: '12px 14px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
              2. 統合後の画像一覧 ({keptImages.length}枚) — 不要な画像は除外し、統合元の画像で必要なものがあれば末尾に追加できます
            </div>
            {loadingPreviews ? (
              <div style={{ fontSize: 13, color: '#94a3b8', padding: 12 }}>画像を読み込み中…</div>
            ) : (
              <>
                {/* Kept images */}
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
                  {keptImages.length === 0 ? (
                    <div style={{ fontSize: 12, color: '#64748b', fontStyle: 'italic' }}>保持する画像がありません</div>
                  ) : (
                    keptImages.map((img, idx) => (
                      <div
                        key={img.key}
                        style={{
                          width: 116, background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                          padding: 6, display: 'flex', flexDirection: 'column', gap: 4,
                        }}
                      >
                        <div style={{ position: 'relative' }}>
                          <a href={img.url} target="_blank" rel="noreferrer" title="新しいタブで開く">
                            <img
                              src={img.url}
                              alt=""
                              style={{ width: '100%', height: 96, objectFit: 'cover', borderRadius: 4, display: 'block', background: '#0f172a' }}
                            />
                          </a>
                          <span style={{
                            position: 'absolute', top: 4, left: 4, background: 'rgba(0,0,0,0.75)',
                            color: '#fff', fontSize: 10, padding: '1px 5px', borderRadius: 4, fontWeight: 700,
                          }}>
                            #{idx + 1}
                          </span>
                        </div>
                        <div style={{ fontSize: 10, color: '#94a3b8', textAlign: 'center' }}>
                          元: Item #{img.sourceItemId} ({img.sourceOrder}枚目)
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 4 }}>
                          <button
                            type="button"
                            className="btn"
                            disabled={idx === 0}
                            onClick={() => moveKeptImage(idx, -1)}
                            style={{ padding: '2px 6px', fontSize: 11 }}
                            title="左へ移動"
                          >←</button>
                          <button
                            type="button"
                            className="btn"
                            disabled={idx === keptImages.length - 1}
                            onClick={() => moveKeptImage(idx, 1)}
                            style={{ padding: '2px 6px', fontSize: 11 }}
                            title="右へ移動"
                          >→</button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => removeKeptImage(img.key)}
                            style={{ padding: '2px 6px', fontSize: 11, background: 'rgba(239,68,68,0.2)', color: '#fca5a5' }}
                            title="この画像を除外"
                          >除外</button>
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Available images from source items (and any removed target images) */}
                {items.map(it => {
                  const imgs = previewsByItem[it.id] || []
                  const unkept = imgs.filter(img => !keptKeySet.has(img.key))
                  if (unkept.length === 0) return null
                  return (
                    <div key={it.id} style={{ borderTop: '1px solid #1e293b', paddingTop: 10, marginTop: 8 }}>
                      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>
                        {it.id === targetId ? `統合先 Item #${it.id} から除外した画像:` : `統合元 Item #${it.id} の画像（必要なものを末尾に追加）:`}
                      </div>
                      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                        {unkept.map(img => (
                          <div
                            key={img.key}
                            style={{
                              width: 116, background: '#1e293b', border: '1px dashed #475569', borderRadius: 6,
                              padding: 6, display: 'flex', flexDirection: 'column', gap: 4, opacity: 0.9,
                            }}
                          >
                            <a href={img.url} target="_blank" rel="noreferrer" title="新しいタブで開く">
                              <img
                                src={img.url}
                                alt=""
                                style={{ width: '100%', height: 96, objectFit: 'cover', borderRadius: 4, display: 'block', background: '#0f172a' }}
                              />
                            </a>
                            <div style={{ fontSize: 10, color: '#94a3b8', textAlign: 'center' }}>
                              {img.sourceOrder}枚目
                            </div>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => addImageToEnd(img)}
                              style={{ padding: '3px 6px', fontSize: 11, background: '#2563eb', color: '#fff' }}
                            >
                              ＋ 末尾に追加
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </>
            )}
          </div>

          {/* 3. Metadata */}
          <div style={{ background: '#0f172a', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>
              3. メタデータ（統合先アイテムに保存する内容）
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div>
                <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>タイトル（カンマ区切り）</label>
                <input
                  type="text"
                  value={titlesText}
                  onChange={e => setTitlesText(e.target.value)}
                  style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>キャラ（カンマ区切り）</label>
                <input
                  type="text"
                  value={charactersText}
                  onChange={e => setCharactersText(e.target.value)}
                  style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>作者</label>
                <input
                  type="text"
                  value={artist}
                  onChange={e => setArtist(e.target.value)}
                  style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>シチュエーション</label>
                <input
                  type="text"
                  value={situation}
                  onChange={e => setSituation(e.target.value)}
                  style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
                />
              </div>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>タグ（カンマ区切り）</label>
              <input
                type="text"
                value={tagsText}
                onChange={e => setTagsText(e.target.value)}
                style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>リンク</label>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                {items.filter(it => it.link).map(it => (
                  <button
                    key={it.id}
                    type="button"
                    className="btn"
                    onClick={() => setLink(it.link)}
                    style={{
                      fontSize: 11, padding: '2px 8px',
                      background: link === it.link ? '#2563eb' : '#1e293b',
                      color: link === it.link ? '#fff' : '#94a3b8',
                      border: '1px solid #334155',
                    }}
                  >
                    #{it.id}のリンクを使う
                  </button>
                ))}
              </div>
              <input
                type="text"
                value={link}
                onChange={e => setLink(e.target.value)}
                style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>本文</label>
              <textarea
                rows={3}
                value={description}
                onChange={e => setDescription(e.target.value)}
                style={{ width: '100%', background: '#1e293b', color: '#f1f5f9', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, boxSizing: 'border-box' }}
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10,
          padding: '12px 20px', borderTop: '1px solid #334155', flexShrink: 0,
        }}>
          <button type="button" className="btn" onClick={onClose} disabled={merging}>
            キャンセル
          </button>
          <button
            type="button"
            className="btn"
            onClick={handleMerge}
            disabled={merging || loadingPreviews}
            style={{ background: '#2563eb', color: '#fff', fontWeight: 600, padding: '8px 20px' }}
          >
            {merging ? 'マージ実行中…' : `Item #${targetItem?.id} にマージする`}
          </button>
        </div>
      </div>
    </div>
  )
}
