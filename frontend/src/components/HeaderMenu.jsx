import React, { useState, useEffect, useRef } from 'react'

// Generic hamburger dropdown for header-level actions (character groups,
// backup, fetch/edit queues, preview timeline, logout, ...) so they don't
// clutter the header as a row of direct buttons.
export default function HeaderMenu({ items }){
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e){
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    function onKey(e){ if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="header-menu" ref={rootRef}>
      <button
        type="button"
        className="btn header-menu-toggle"
        onClick={() => setOpen(o => !o)}
        aria-label="メニュー"
        aria-expanded={open}
        title="メニュー"
      >☰</button>
      {open && (
        <div className="header-menu-dropdown">
          {items.map((item, i) => (
            item.divider ? (
              <div key={i} className="header-menu-divider" />
            ) : (
              <button
                key={i}
                type="button"
                className={`header-menu-item${item.active ? ' active' : ''}`}
                onClick={() => { item.onClick(); setOpen(false) }}
              >
                <span>{item.label}</span>
                {item.badge != null && <span className="header-menu-badge">{item.badge}</span>}
              </button>
            )
          ))}
        </div>
      )}
    </div>
  )
}
