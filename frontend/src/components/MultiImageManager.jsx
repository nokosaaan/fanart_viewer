import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { notify } from '../lib/crossWindowSync'
import { getPlatformIcon } from '../lib/platformIcon'

const DISMISSED_STORAGE_KEY = 'fv_multi_image_dismissed_ids'

function getDismissedIds() {
  try {
    const raw = localStorage.getItem(DISMISSED_STORAGE_KEY)
    return raw ? new Set(JSON.parse(raw)) : new Set()
  } catch {
    return new Set()
  }
}

function saveDismissedIds(set) {
  try {
    localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify(Array.from(set)))
  } catch {}
}

function MultiItemRow({ item, onOpenPreview, onUpdated, onDismiss, onRestore, isDismissed, readOnly }) {
  const [previews, setPreviews] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [deleting, setDeleting] = useState(false)
  const [deletingItem, setDeletingItem] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [done, setDone] = useState(false)

  const loadPreviews = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch(`/api/items/${item.id}/previews/`)
      if (!resp.ok) return
      const j = await resp.json()
      if (Array.isArray(j)) {
        setPreviews(j)
        if (j.length <= 1) setDone(true)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [item.id])

  useEffect(() => {
    loadPreviews()
  }, [loadPreviews])

  function toggleSelect(id) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function selectAll() {
    if (!previews) return
    setSelectedIds(new Set(previews.map(p => p.id)))
  }

  function clearSelect() {
    setSelectedIds(new Set())
  }

  async function runDelete(body, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return
    setDeleting(true)
    try {
      const resp = await fetch(`/api/items/${item.id}/delete_previews/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        alert('削除に失敗しました: ' + (j.detail || j.error || resp.status))
        return
      }
      setSelectedIds(new Set())
      await loadPreviews()
      notify('item-preview-updated', { id: item.id })
      if (onUpdated) onUpdated(item.id, j.remaining)
      if (j.remaining <= 1) setDone(true)
    } catch (e) {
      console.error(e)
      alert('削除に失敗しました')
    } finally {
      setDeleting(false)
    }
  }

  // 1枚目だけ残す（他の画像をすべて削除）※この画面に限り確認アラートなしで即座に実行
  function keepFirstOnly() {
    if (!previews || previews.length <= 1) return
    const first = previews[0]
    runDelete({ keep_ids: [first.id] }, null)
  }

  // 選択した画像を削除
  function deleteSelected() {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    runDelete(
      { delete_ids: ids },
      `選択した ${ids.length} 枚の画像を削除しますか？ 元に戻せません。`
    )
  }

  // 選択した画像だけ残す（それ以外を削除）
  function keepSelected() {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    const removeCount = (previews ? previews.length : 0) - ids.length
    if (removeCount <= 0) {
      alert('削除される画像がありません（すべて選択されています）')
      return
    }
    runDelete(
      { keep_ids: ids },
      `選択した ${ids.length} 枚だけを残し、それ以外の ${removeCount} 枚を削除しますか？ 元に戻せません。`
    )
  }

  // 全画像削除
  async function deleteAll() {
    if (!window.confirm(`このアイテムの画像（全${previews ? previews.length : 0}枚）をすべて削除しますか？`)) return
    setDeleting(true)
    try {
      const resp = await fetch(`/api/items/${item.id}/previews/`, { method: 'DELETE' })
      if (!resp.ok) {
        alert('画像の全削除に失敗しました')
        return
      }
      setPreviews([])
      setDone(true)
      notify('item-preview-updated', { id: item.id })
      if (onUpdated) onUpdated(item.id, 0)
    } catch (e) {
      console.error(e)
      alert('画像の全削除に失敗しました')
    } finally {
      setDeleting(false)
    }
  }

  // アイテムそのものをDBから削除
  async function deleteWholeItem() {
    if (!window.confirm(`作品「${item.title || item.id}」をデータベースから完全に削除しますか？`)) return
    setDeletingItem(true)
    try {
      const resp = await fetch(`/api/items/${item.id}/delete_item/`, { method: 'DELETE' })
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}))
        alert('アイテムの削除に失敗しました: ' + (j.detail || j.error || resp.status))
        return
      }
      notify('item-deleted', { id: item.id })
      if (onUpdated) onUpdated(item.id, 0)
    } catch (e) {
      console.error(e)
      alert('アイテムの削除に失敗しました')
    } finally {
      setDeletingItem(false)
    }
  }

  const title = (item.titles && item.titles[0]) || item.title || 'Untitled'
  const platform = getPlatformIcon(item.link)

  return (
    <div style={{
      background: 'var(--card)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
      opacity: (done || isDismissed) ? 0.6 : 1,
      transition: 'opacity 0.2s'
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
          <span style={{ fontSize: 11, color: 'var(--text-faint)', fontFamily: 'monospace' }}>#{item.id}</span>
          <span style={{ fontWeight: 600, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={title}>
            {title}
          </span>
          {item.artist && (
            <span style={{ fontSize: 12, color: 'var(--muted)' }} title={item.artist}>
              by {item.artist}
            </span>
          )}
          {item.link && (
            <a href={item.link} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: '#38bdf8' }} title={item.link}>
              {platform && <img src={platform.icon} alt="" style={{ width: 14, height: 14, borderRadius: 2 }} />}
              <span style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>開く</span>
            </a>
          )}
          {previews && (
            <span style={{
              fontSize: 11,
              fontWeight: 600,
              padding: '2px 6px',
              borderRadius: 12,
              background: isDismissed
                ? 'rgba(148, 163, 184, 0.2)'
                : done
                ? 'rgba(34, 197, 94, 0.2)'
                : 'rgba(56, 189, 248, 0.2)',
              color: isDismissed
                ? '#94a3b8'
                : done
                ? '#4ade80'
                : '#38bdf8',
              border: isDismissed
                ? '1px solid rgba(148, 163, 184, 0.4)'
                : done
                ? '1px solid rgba(34, 197, 94, 0.4)'
                : '1px solid rgba(56, 189, 248, 0.4)'
            }}>
              {isDismissed ? `維持済み (${previews.length}枚)` : done ? `整理済 (${previews.length}枚)` : `全 ${previews.length} 枚`}
            </span>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {/* 問題なければこのまま維持して一覧から除外するボタン */}
          {!isDismissed ? (
            <button
              className="btn"
              onClick={() => onDismiss && onDismiss(item.id)}
              style={{ fontSize: 12, padding: '4px 8px', background: '#059669', color: '#fff', fontWeight: 600 }}
              title="この作品の画像に問題はないため全枚数維持し、この整理一覧から完了として除外します"
            >
              ✓ このまま維持 (一覧から除外)
            </button>
          ) : (
            <button
              className="btn"
              onClick={() => onRestore && onRestore(item.id)}
              style={{ fontSize: 11, padding: '4px 8px', color: 'var(--muted)' }}
              title="再度この作品を未確認一覧に戻します"
            >
              ↩ 一覧に戻す
            </button>
          )}

          {!readOnly && previews && previews.length > 1 && (
            <>
              <button
                className="btn"
                onClick={keepFirstOnly}
                disabled={deleting}
                style={{ fontSize: 12, padding: '4px 8px', background: '#2563eb', color: '#fff', fontWeight: 600 }}
                title="1枚目のみ残し、2枚目以降の余計な画像をすべて削除します（確認不要で即実行）"
              >
                {deleting ? '…' : '★ 1枚目だけ残す'}
              </button>
              <button
                className="btn"
                onClick={deleteAll}
                disabled={deleting}
                style={{ fontSize: 12, padding: '4px 8px', background: '#ea580c', color: '#fff' }}
                title="この投稿の画像をすべて削除します"
              >
                全画像クリア
              </button>
            </>
          )}

          {!readOnly && (
            <button
              className="btn"
              onClick={deleteWholeItem}
              disabled={deletingItem}
              style={{ fontSize: 12, padding: '4px 8px', background: '#a33', color: '#fff' }}
              title="この作品（アイテム）そのものをデータベースから完全に削除します"
            >
              {deletingItem ? '…' : '🗑 アイテム削除'}
            </button>
          )}

          <button
            className="btn"
            onClick={() => onOpenPreview && onOpenPreview(item.id)}
            style={{ fontSize: 12, padding: '4px 8px' }}
            title="詳細プレビュー (漫画モード / 拡大表示) を開く"
          >
            🔍 プレビュー
          </button>
          <button
            className="btn"
            onClick={() => setCollapsed(c => !c)}
            style={{ fontSize: 12, padding: '4px 6px', color: 'var(--text-faint)' }}
            title={collapsed ? '展開' : '折りたたむ'}
          >
            {collapsed ? '▼' : '▲'}
          </button>
        </div>
      </div>

      {/* Thumbnails filmstrip */}
      {!collapsed && (
        <>
          {loading && <div style={{ fontSize: 12, color: 'var(--muted)', padding: '12px 0' }}>画像を読み込み中…</div>}
          {previews && previews.length > 0 && (
            <div style={{
              display: 'flex',
              gap: 8,
              overflowX: 'auto',
              padding: '6px 2px 10px',
              scrollbarWidth: 'thin',
              alignItems: 'flex-start'
            }}>
              {previews.map((p, idx) => {
                const isSelected = selectedIds.has(p.id)
                return (
                  <div
                    key={p.id}
                    style={{
                      flex: '0 0 auto',
                      width: 104,
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: 4,
                      position: 'relative'
                    }}
                  >
                    <div
                      style={{
                        position: 'relative',
                        width: 100,
                        height: 110,
                        borderRadius: 6,
                        overflow: 'hidden',
                        border: isSelected ? '2px solid #3b82f6' : '1px solid var(--border)',
                        background: '#000',
                        cursor: 'pointer',
                        boxShadow: isSelected ? '0 0 8px rgba(59, 130, 246, 0.5)' : 'none'
                      }}
                      onClick={() => !readOnly && toggleSelect(p.id)}
                      title={`クリックして選択切替 (${idx + 1}/${previews.length})`}
                    >
                      <img
                        src={`/api/items/${item.id}/preview/?index=${p.index}`}
                        alt=""
                        loading="lazy"
                        style={{
                          width: '100%',
                          height: '100%',
                          objectFit: 'contain'
                        }}
                      />
                      <span style={{
                        position: 'absolute',
                        top: 2,
                        left: 2,
                        background: 'rgba(0,0,0,0.7)',
                        color: '#fff',
                        fontSize: 9,
                        padding: '1px 4px',
                        borderRadius: 2
                      }}>
                        {idx + 1}
                      </span>
                      {!readOnly && (
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(p.id)}
                          style={{
                            position: 'absolute',
                            top: 4,
                            right: 4,
                            cursor: 'pointer',
                            accentColor: '#3b82f6',
                            transform: 'scale(1.2)'
                          }}
                          onClick={e => e.stopPropagation()}
                        />
                      )}
                    </div>
                    <span style={{
                      fontSize: 10,
                      color: 'var(--text-faint)',
                      maxWidth: 100,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap'
                    }} title={p.filename || `Page ${idx + 1}`}>
                      {p.filename || `Page ${idx + 1}`}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {/* Batch action bar for selected items */}
          {!readOnly && previews && previews.length > 1 && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              padding: '6px 10px',
              background: 'var(--hover-bg)',
              borderRadius: 6,
              flexWrap: 'wrap'
            }}>
              <span style={{ color: selectedIds.size > 0 ? '#38bdf8' : 'var(--muted)', fontWeight: 600 }}>
                {selectedIds.size} 枚選択中
              </span>
              <button className="btn" style={{ fontSize: 11, padding: '3px 7px' }} onClick={selectAll}>すべて選択</button>
              <button className="btn" style={{ fontSize: 11, padding: '3px 7px' }} onClick={clearSelect} disabled={selectedIds.size === 0}>選択解除</button>
              <span style={{ width: 1, height: 14, background: 'var(--divider)', margin: '0 2px' }} />
              <button
                className="btn"
                style={{ fontSize: 11, padding: '3px 10px', background: '#a33', color: '#fff' }}
                onClick={deleteSelected}
                disabled={deleting || selectedIds.size === 0}
                title="チェックを入れた画像のみを削除します"
              >
                選んだ画像を削除
              </button>
              <button
                className="btn"
                style={{ fontSize: 11, padding: '3px 10px', background: '#166534', color: '#fff' }}
                onClick={keepSelected}
                disabled={deleting || selectedIds.size === 0}
                title="チェックを入れた画像のみを残し、それ以外を削除します"
              >
                選んだ画像だけ残す
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function MultiImageManager({ onClose, allItems, onOpenPreview, readOnly }) {
  const [filterQuery, setFilterQuery] = useState('')
  const [showDismissed, setShowDismissed] = useState(false)
  const [dismissedIds, setDismissedIds] = useState(getDismissedIds)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 15

  function handleDismiss(id) {
    setDismissedIds(prev => {
      const next = new Set(prev)
      next.add(id)
      saveDismissedIds(next)
      return next
    })
  }

  function handleRestore(id) {
    setDismissedIds(prev => {
      const next = new Set(prev)
      next.delete(id)
      saveDismissedIds(next)
      return next
    })
  }

  // 複数枚画像を持つアイテムを抽出
  const multiItems = useMemo(() => {
    return (allItems || []).filter(it => {
      if (!it || !it.has_preview) return false
      if (typeof it.preview_count === 'number') {
        return it.preview_count > 1
      }
      return true
    })
  }, [allItems])

  const filtered = useMemo(() => {
    let list = multiItems
    // 「問題なしとして除外」されたアイテムを通常は非表示、showDismissed===trueなら含める
    if (!showDismissed) {
      list = list.filter(it => !dismissedIds.has(it.id))
    }
    if (!filterQuery.trim()) return list
    const q = filterQuery.toLowerCase().trim()
    return list.filter(it => {
      const titles = Array.isArray(it.titles) ? it.titles.join(' ') : (it.title || '')
      const artist = it.artist || ''
      const tags = Array.isArray(it.tags) ? it.tags.join(' ') : ''
      return titles.toLowerCase().includes(q) || artist.toLowerCase().includes(q) || tags.toLowerCase().includes(q) || String(it.id).includes(q)
    })
  }, [multiItems, filterQuery, showDismissed, dismissedIds])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const pageItems = useMemo(() => {
    return filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  }, [filtered, page])

  const dismissedCount = useMemo(() => {
    return multiItems.filter(it => dismissedIds.has(it.id)).length
  }, [multiItems, dismissedIds])

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div
        className="cgm-panel"
        style={{ width: 960, maxWidth: '96vw', height: '88vh', display: 'flex', flexDirection: 'column' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="cgm-panel-header" style={{ padding: '14px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>🖼️</span>
            <div>
              <strong style={{ fontSize: 16 }}>複数枚画像の整理</strong>
              <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10 }}>
                (未確認: {multiItems.length - dismissedCount} 件 / 全体: {multiItems.length} 件)
              </span>
            </div>
          </div>
          <button className="cgm-panel-close" onClick={onClose} aria-label="閉じる">✕</button>
        </div>

        {/* Search & Help bar */}
        <div style={{
          padding: '10px 20px',
          borderBottom: '1px solid var(--divider)',
          background: 'var(--hover-bg)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap'
        }}>
          <div style={{ fontSize: 12, color: 'var(--muted)', flex: 1, minWidth: 260 }}>
            💡 複数枚画像で問題ない作品は<strong>「✓ このまま維持 (一覧から除外)」</strong>で消化できます。不要な画像は「1枚目だけ残す」や「選択削除」で整理できます。
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <label style={{ fontSize: 12, color: 'var(--text)', display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer', userSelect: 'none' }}>
              <input
                type="checkbox"
                checked={showDismissed}
                onChange={e => setShowDismissed(e.target.checked)}
              />
              <span>維持済み ({dismissedCount}件) も表示</span>
            </label>
            <input
              type="text"
              className="cgm-search-input"
              placeholder="タイトル・作者名・IDで絞り込み…"
              value={filterQuery}
              onChange={e => { setFilterQuery(e.target.value); setPage(0) }}
              style={{ width: 220 }}
            />
          </div>
        </div>

        {/* Items List */}
        <div style={{
          flex: '1 1 auto',
          overflowY: 'auto',
          padding: '16px 20px',
          display: 'flex',
          flexDirection: 'column',
          gap: 12
        }}>
          {filtered.length === 0 ? (
            <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--muted)', fontSize: 14 }}>
              {showDismissed
                ? '複数枚の画像を保持している作品はありません'
                : '未確認の複数枚画像作品はありません（すべて維持済みまたは整理完了です！）'}
            </div>
          ) : (
            pageItems.map(item => (
              <MultiItemRow
                key={item.id}
                item={item}
                onOpenPreview={onOpenPreview}
                onDismiss={handleDismiss}
                onRestore={handleRestore}
                isDismissed={dismissedIds.has(item.id)}
                readOnly={readOnly}
              />
            ))
          )}
        </div>

        {/* Footer with pagination */}
        <div style={{
          padding: '10px 20px',
          borderTop: '1px solid var(--divider)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: 13
        }}>
          <span style={{ color: 'var(--muted)' }}>
            全 {filtered.length} 件中 {filtered.length === 0 ? 0 : page * PAGE_SIZE + 1} 〜 {Math.min((page + 1) * PAGE_SIZE, filtered.length)} 件を表示
          </span>
          {totalPages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <button
                className="btn"
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                style={{ padding: '4px 10px' }}
              >
                前へ
              </button>
              <span>{page + 1} / {totalPages}</span>
              <button
                className="btn"
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                style={{ padding: '4px 10px' }}
              >
                次へ
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
