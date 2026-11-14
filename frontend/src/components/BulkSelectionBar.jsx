import React, { useState, useEffect } from 'react'
import ItemMergeModal from './ItemMergeModal'
import { notify } from '../lib/crossWindowSync'

export function useItemSelection(items) {
  const [selectedIds, setSelectedIds] = useState(new Set())

  // Selection is scoped to "this page" — keying the reset on the actual ids
  // shown means a page/filter/search change clears it, while updating an item
  // in place (same ids, new array ref) preserves the selection.
  const idsKey = (items || []).map(it => it && it.id).join(',')
  useEffect(() => {
    setSelectedIds(new Set())
  }, [idsKey])

  function toggleSelect(id) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    setSelectedIds(prev =>
      prev.size === items.length ? new Set() : new Set(items.map(it => it.id))
    )
  }

  function clearSelection() {
    setSelectedIds(new Set())
  }

  return { selectedIds, setSelectedIds, toggleSelect, toggleSelectAll, clearSelection }
}

export default function BulkSelectionBar({
  items,
  selectedIds,
  setSelectedIds,
  onToggleSelectAll,
  onClearSelection,
}) {
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [mergeItems, setMergeItems] = useState(null)

  async function bulkDelete() {
    const ids = [...selectedIds]
    if (ids.length === 0) return
    const ok = window.confirm(`選択した${ids.length}件のアイテムを削除しますか？ プレビュー画像も含めて削除され、元に戻せません。`)
    if (!ok) return
    setBulkDeleting(true)
    let deleted = 0, failed = 0
    for (const id of ids) {
      try {
        const resp = await fetch(`/api/items/${id}/delete_item/`, { method: 'DELETE' })
        if (resp.ok) {
          deleted++
          notify('item-deleted', { id })
          setSelectedIds(prev => {
            const next = new Set(prev)
            next.delete(id)
            return next
          })
        } else {
          failed++
        }
      } catch (e) {
        console.error('Bulk delete failed for item', id, e)
        failed++
      }
    }
    setBulkDeleting(false)
    if (failed > 0) alert(`${deleted}件を削除しました。${failed}件は削除に失敗しました。`)
  }

  if (!items || items.length === 0) return null

  return (
    <>
      <div className="options-panel" style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-start', marginBottom: 10 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}>
          <input
            className="fv-checkbox-lg"
            type="checkbox"
            checked={selectedIds.size > 0 && selectedIds.size === items.length}
            ref={el => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < items.length }}
            onChange={onToggleSelectAll}
          />
          このページを全選択
        </label>
        {selectedIds.size > 0 && (
          <>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{selectedIds.size}件選択中</span>
            {selectedIds.size >= 2 && (
              <button
                className="btn"
                style={{ background: '#2563eb', color: '#fff' }}
                onClick={() => setMergeItems(items.filter(it => selectedIds.has(it.id)))}
                disabled={bulkDeleting}
              >
                選択した{selectedIds.size}件をマージ
              </button>
            )}
            <button className="btn" style={{ background: '#a33', color: '#fff' }} onClick={bulkDelete} disabled={bulkDeleting}>
              {bulkDeleting ? '削除中…' : `選択した${selectedIds.size}件を削除`}
            </button>
          </>
        )}
      </div>
      {mergeItems && (
        <ItemMergeModal
          items={mergeItems}
          onClose={() => setMergeItems(null)}
          onMerged={() => {
            setMergeItems(null)
            onClearSelection()
          }}
        />
      )}
    </>
  )
}
