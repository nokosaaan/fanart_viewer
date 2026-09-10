import React, { useState } from 'react'

// Builds the list of page numbers to render, 1-indexed: always the first
// `windowSize` pages, the current page +/-1 (so nearby pages are one click
// away wherever you are), and the last page — with an ellipsis marker
// wherever there's a gap. For page 1 of a long list this reduces to
// exactly "first N pages ... last page" (see ui-design-dictionary's
// Pagination entry — the Google/GitHub-Issues convention this mirrors).
function buildPageList(currentPage1, totalPages, windowSize = 5) {
  const pages = new Set()
  for (let i = 1; i <= Math.min(windowSize, totalPages); i++) pages.add(i)
  for (let i = Math.max(1, currentPage1 - 1); i <= Math.min(totalPages, currentPage1 + 1); i++) pages.add(i)
  if (totalPages > 0) pages.add(totalPages)

  const sorted = [...pages].sort((a, b) => a - b)
  const result = []
  let prev = null
  for (const p of sorted) {
    if (prev !== null && p - prev > 1) result.push({ ellipsis: true, key: `e${p}` })
    result.push({ page: p, key: p })
    prev = p
  }
  return result
}

// `page`/`totalPages` are 0-indexed to match App.jsx's own pageIndex state;
// onGoToPage receives a 0-indexed target. The manual jump-to-page input is
// kept alongside the number buttons (not a replacement for it) — clicking
// through page buttons only ever reaches the first window/last page/pages
// adjacent to the current one, so jumping to an arbitrary page still needs
// direct entry.
export default function Pagination({ page, totalPages, onGoToPage, onPrev, onNext, prevDisabled, nextDisabled, resultsLabel }) {
  const [inputVal, setInputVal] = useState('')
  const current1 = page + 1
  const items = buildPageList(current1, totalPages)

  return (
    <div className="pagination-controls">
      <button className="btn" onClick={onPrev} disabled={prevDisabled}>Prev</button>
      <div className="pagination-pages">
        {items.map(item => (
          item.ellipsis
            ? <span key={item.key} className="pagination-ellipsis">…</span>
            : (
              <button
                key={item.key}
                className={`pagination-page-btn${item.page === current1 ? ' current' : ''}`}
                onClick={() => onGoToPage(item.page - 1)}
                disabled={item.page === current1}
              >
                {item.page}
              </button>
            )
        ))}
      </div>
      <input
        type="number"
        min={1}
        max={totalPages}
        value={inputVal}
        onChange={e => setInputVal(e.target.value)}
        placeholder="ページ番号"
        onKeyDown={e => {
          if (e.key === 'Enter') {
            const v = parseInt(inputVal, 10)
            if (!isNaN(v)) onGoToPage(v - 1)
            setInputVal('')
            e.target.blur()
          } else if (e.key === 'Escape') {
            setInputVal('')
            e.target.blur()
          }
        }}
        onBlur={() => setInputVal('')}
        style={{ width: 72, textAlign: 'center', padding: '2px 4px' }}
        title="ページ番号を直接入力してジャンプ"
      />
      <button className="btn" onClick={onNext} disabled={nextDisabled}>Next</button>
      {resultsLabel && <span className="pagination-results-label">{resultsLabel}</span>}
    </div>
  )
}
