import React, { useState, useEffect, useRef } from 'react'

// One top-level entry: either a plain action (item.onClick), or a
// labeled group (item.submenu: [...]) that expands its own items inline,
// indented — used to cluster related tools (character setup, Twitter,
// Pixiv, each with their own auth/poller settings alongside their
// fetch actions) instead of a single long flat list. A submenu item
// itself is never clickable — only its children are.
function MenuEntry({ item, onAction }){
  // Submenus default to collapsed local state, but Tour.jsx (see
  // lib/tourSteps.js) needs to force a SPECIFIC one open to spotlight an
  // item inside it — rather than lifting this into a controlled prop
  // (every submenu, everywhere, just to support one feature), the tour
  // instead finds this button via its own data-tour id and calls the DOM
  // .click() on it directly (a real click event, so this exact handler
  // still runs) whenever aria-expanded says it isn't open yet. Nothing
  // here needs to change to support that.
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
          data-tour={item.tourId}
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
                  data-tour={sub.tourId}
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
      data-tour={item.tourId}
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
// `open`/`onOpenChange`: optional controlled-mode pair — omit both (as
// every caller but Tour.jsx does) and this manages its own open/closed
// state exactly as before. The tour needs to force this open (and keep it
// open across several steps that each spotlight a different menu item)
// from OUTSIDE this component, which plain internal state can't support.
export default function HeaderMenu({ items, open: openProp, onOpenChange }){
  const [openState, setOpenState] = useState(false)
  const open = openProp !== undefined ? openProp : openState
  const setOpen = onOpenChange || setOpenState
  const rootRef = useRef(null)

  useEffect(() => {
    // In controlled mode (Tour.jsx), the controller decides when this
    // closes — an outside click during the tour is most likely on the
    // tour's own overlay/tooltip (rendered outside rootRef), which would
    // otherwise close the menu out from under it mid-tour.
    if (!open || openProp !== undefined) return
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
  }, [open, openProp])

  return (
    <div className="header-menu" ref={rootRef}>
      <button
        type="button"
        className="btn header-menu-toggle"
        onClick={() => setOpen(o => !o)}
        aria-label="メニュー"
        aria-expanded={open}
        title="メニュー"
        data-tour="header-menu-toggle"
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
