import React, { useState, useEffect, useRef } from 'react'

// One top-level entry: either a plain action (item.onClick), or a
// labeled group (item.submenu: [...]) that expands its own items inline,
// indented — used to cluster related tools (character setup, Twitter,
// Pixiv, each with their own auth/poller settings alongside their
// fetch actions) instead of a single long flat list. A submenu item
// itself is never clickable — only its children are.
function MenuEntry({ item, onAction }){
  const [subOpen, setSubOpen] = useState(false)

  if (item.divider) return <div className="header-menu-divider" />

  if (Array.isArray(item.submenu)) {
    return (
      <div className="header-menu-group">
        <button
          type="button"
          className="header-menu-item header-menu-group-toggle"
          onClick={() => setSubOpen(o => !o)}
          aria-expanded={subOpen}
        >
          <span>{item.label}</span>
          <span className="header-menu-group-arrow">{subOpen ? '▾' : '▸'}</span>
        </button>
        {subOpen && (
          <div className="header-menu-submenu">
            {item.submenu.map((sub, i) => (
              sub.divider ? (
                <div key={i} className="header-menu-divider" />
              ) : (
                <button
                  key={i}
                  type="button"
                  className={`header-menu-item${sub.active ? ' active' : ''}`}
                  onClick={() => onAction(sub)}
                >
                  <span>{sub.label}</span>
                  {sub.badge != null && <span className="header-menu-badge">{sub.badge}</span>}
                </button>
              )
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <button
      type="button"
      className={`header-menu-item${item.active ? ' active' : ''}`}
      onClick={() => onAction(item)}
    >
      <span>{item.label}</span>
      {item.badge != null && <span className="header-menu-badge">{item.badge}</span>}
    </button>
  )
}

// Generic hamburger dropdown for header-level actions (character groups,
// backup, fetch/edit queues, preview timeline, logout, ...) so they don't
// clutter the header as a row of direct buttons. Each top-level entry is
// either a plain action or a `submenu` group (see MenuEntry above).
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
            <MenuEntry key={i} item={item} onAction={sub => { sub.onClick(); setOpen(false) }} />
          ))}
        </div>
      )}
    </div>
  )
}
