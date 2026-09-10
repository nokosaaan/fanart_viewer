import React, { useState, useEffect, useRef } from 'react'

// Generic spotlight/tooltip walkthrough engine — see lib/tourSteps.js for
// the actual step content (App.jsx wires the two together). Deliberately
// small and dependency-free rather than pulling in a tour library: every
// target here is a real element already in this app's own DOM (menu items,
// mostly — see tourSteps.js's own reasoning on why), found via a
// `data-tour="..."` attribute rather than fragile text/class matching.
//
// `steps`: [{ title, body, targetId?, groupToggleId?, needsHeaderMenu? }, ...].
//   targetId: data-tour value of the element to spotlight; omitted for a
//     centered, non-spotlit card (used for anything with no reliably-
//     present target, e.g. per-item action icons that don't exist when
//     the item list is empty).
//   groupToggleId: data-tour value of a submenu's OWN toggle button
//     (see HeaderMenu.jsx's MenuEntry) — clicked via the real DOM .click()
//     if aria-expanded says it isn't open yet, so an item nested inside a
//     collapsed submenu becomes findable before this step tries to locate
//     targetId. Left expanded afterward (harmless — the whole dropdown is
//     hidden once the header menu itself is closed).
//   needsHeaderMenu: whether THIS step's target lives inside the header
//     dropdown — `onMenuNeed(bool)` is called on every step change so the
//     caller (App.jsx) can open/close it exactly when needed, rather than
//     leaving it open for an entire tour: a step targeting something
//     OUTSIDE the menu (the search bar, its filter chips) needs it
//     closed, since the open dropdown physically overlaps and hides that
//     part of the screen otherwise.
export default function Tour({ steps, onClose, onMenuNeed }){
  const [idx, setIdx] = useState(0)
  const [rect, setRect] = useState(null) // DOMRect | 'not-found' | null (centered)
  // The target's own containing dropdown (see HeaderMenu.jsx's
  // .header-menu-dropdown), when there is one — cardStyle's fallback
  // placement (neither side has room, e.g. a narrower/non-maximized
  // window) uses THIS instead of the single target's own rect, so the
  // card lands below/above the whole dropdown column rather than
  // overlapping a sibling item packed right next to the target.
  const [dropdownRect, setDropdownRect] = useState(null)
  const step = steps[idx]

  useEffect(() => {
    let cancelled = false
    setRect(null)
    // Both of these are just requests, not guarantees this same tick:
    // onMenuNeed(true) triggers a state update in App.jsx that opens the
    // header menu on its NEXT render, and even once that dropdown exists,
    // a groupToggleId's own submenu is a SEPARATE bit of state (MenuEntry's
    // own, local) that only starts existing once the toggle button itself
    // is in the DOM to be clicked. Retrying both inside tryFind's own
    // polling loop below (rather than doing them once, synchronously,
    // right here) is what actually gives each render a chance to catch up
    // before giving up.
    if (onMenuNeed) onMenuNeed(!!step.needsHeaderMenu)

    // Opening a menu/submenu takes a render cycle (or two) to actually
    // paint the target into the DOM -- poll briefly instead of assuming
    // it's already there the instant this effect runs.
    let attempts = 0
    function tryFind(){
      if (cancelled) return
      if (step.groupToggleId) {
        const toggle = document.querySelector(`[data-tour="${step.groupToggleId}"]`)
        if (toggle && toggle.getAttribute('aria-expanded') === 'false') toggle.click()
      }
      if (!step.targetId) { setRect(null); setDropdownRect(null); return }
      const el = document.querySelector(`[data-tour="${step.targetId}"]`)
      if (el) {
        setRect(el.getBoundingClientRect())
        const dropdown = el.closest('.header-menu-dropdown')
        setDropdownRect(dropdown ? dropdown.getBoundingClientRect() : null)
        el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      } else if (attempts < 20) {
        attempts++
        setTimeout(tryFind, 100)
      } else {
        setRect('not-found')
      }
    }
    tryFind()

    function onReflow(){
      if (!step.targetId) return
      const el = document.querySelector(`[data-tour="${step.targetId}"]`)
      if (el) {
        setRect(el.getBoundingClientRect())
        const dropdown = el.closest('.header-menu-dropdown')
        setDropdownRect(dropdown ? dropdown.getBoundingClientRect() : null)
      }
    }
    window.addEventListener('resize', onReflow)
    window.addEventListener('scroll', onReflow, true)
    return () => {
      cancelled = true
      window.removeEventListener('resize', onReflow)
      window.removeEventListener('scroll', onReflow, true)
    }
  }, [idx]) // eslint-disable-line react-hooks/exhaustive-deps

  function next(){ idx >= steps.length - 1 ? onClose('finished') : setIdx(i => i + 1) }
  function back(){ setIdx(i => Math.max(0, i - 1)) }
  function skip(){ onClose('skipped') }

  const hasSpotlight = rect && rect !== 'not-found'
  const PAD = 8

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 5000 }}>
      {hasSpotlight ? (
        <>
          <div style={dim(0, 0, '100%', Math.max(0, rect.top - PAD))} />
          <div style={dim(0, rect.bottom + PAD, '100%', `calc(100vh - ${rect.bottom + PAD}px)`)} />
          <div style={dim(0, rect.top - PAD, Math.max(0, rect.left - PAD), rect.height + PAD * 2)} />
          <div style={dim(rect.right + PAD, rect.top - PAD, `calc(100vw - ${rect.right + PAD}px)`, rect.height + PAD * 2)} />
          {/* Highlight ring, purely visual */}
          <div style={{
            position: 'fixed', left: rect.left - PAD, top: rect.top - PAD,
            width: rect.width + PAD * 2, height: rect.height + PAD * 2,
            borderRadius: 8, boxShadow: '0 0 0 3px #3b82f6, 0 0 24px rgba(59,130,246,0.5)',
            pointerEvents: 'none', transition: 'all 0.15s ease',
          }} />
          {/* Transparent — but click-capturing — cover directly over the
              spotlighted element itself: the tour intentionally never lets
              the real button underneath receive the click (which could
              e.g. close the very menu this step just forced open), so
              stepping through only ever happens via this panel's own
              buttons. */}
          <div style={{
            position: 'fixed', left: rect.left - PAD, top: rect.top - PAD,
            width: rect.width + PAD * 2, height: rect.height + PAD * 2,
            background: 'transparent', pointerEvents: 'auto',
          }} />
        </>
      ) : (
        <div style={dim(0, 0, '100%', '100%')} />
      )}

      <div style={cardStyle(rect, dropdownRect)}>
        <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>{idx + 1} / {steps.length}</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: '#f8fafc', marginBottom: 8 }}>{step.title}</div>
        <div style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.6, marginBottom: 16, whiteSpace: 'pre-wrap' }}>{step.body}</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <button className="btn" style={{ background: 'transparent', color: '#94a3b8' }} onClick={skip}>スキップ</button>
          <div style={{ display: 'flex', gap: 8 }}>
            {idx > 0 && <button className="btn" style={{ background: '#334155' }} onClick={back}>戻る</button>}
            <button className="btn" onClick={next}>{idx >= steps.length - 1 ? '完了' : '次へ'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function dim(left, top, width, height){
  return { position: 'fixed', left, top, width, height, background: 'rgba(15,23,42,0.75)', pointerEvents: 'auto' }
}

const CARD_WIDTH = 320
const CARD_HEIGHT_ESTIMATE = 180 // rough; only used to keep the card on-screen vertically, not for layout
const MARGIN = 16

function cardStyle(rect, dropdownRect){
  if (!rect || rect === 'not-found') {
    return {
      position: 'fixed', zIndex: 5001, width: CARD_WIDTH, maxWidth: '90vw',
      background: '#1e293b', border: '1px solid #334155', borderRadius: 10,
      padding: 18, boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
      left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
    }
  }
  // clientWidth/clientHeight (excludes any scrollbar) rather than
  // window.innerWidth/innerHeight (includes it) — the more conservative
  // of the two, so the card never ends up partly behind a scrollbar.
  const vw = document.documentElement.clientWidth || window.innerWidth
  const vh = document.documentElement.clientHeight || window.innerHeight
  // Actual rendered width, not just the CSS max-width safety net — the
  // POSITION math below needs to agree with this, not the fixed
  // CARD_WIDTH, or a narrower-than-usual window (not maximized) could
  // still place the card such that the (CSS-shrunk) box either overflows
  // the edge it's anchored away from or leaves an oddly large gap.
  const width = Math.min(CARD_WIDTH, vw - MARGIN * 2)
  const base = {
    position: 'fixed', zIndex: 5001, width, maxWidth: '90vw',
    background: '#1e293b', border: '1px solid #334155', borderRadius: 10,
    padding: 18, boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
  }

  // Prefer placing the card beside the target (left, then right),
  // vertically centered on it and clamped to stay fully on-screen —
  // this is what actually fixes two real problems a plain above/below
  // placement had: a target near the right edge (the header menu toggle,
  // or any item inside its dropdown) pushed the card off-screen to the
  // right, and a target packed closely among siblings in a tall, narrow
  // list (the dropdown itself) made an above/below card overlap the
  // sibling right next to it. Side placement avoids both, since it's
  // offset onto a part of the screen the list/dropdown doesn't occupy.
  const spaceLeft = rect.left
  const spaceRight = vw - rect.right
  const centeredTop = clamp(
    rect.top + rect.height / 2 - CARD_HEIGHT_ESTIMATE / 2,
    MARGIN, vh - CARD_HEIGHT_ESTIMATE - MARGIN,
  )

  if (spaceLeft >= width + MARGIN * 2) {
    return { ...base, left: rect.left - width - MARGIN, top: centeredTop }
  }
  if (spaceRight >= width + MARGIN * 2) {
    return { ...base, left: rect.right + MARGIN, top: centeredTop }
  }

  // Neither side has room -- typically means the window itself is
  // narrower than usual (not maximized), not just that this particular
  // target is wide. If the target lives inside a dropdown, fall back to
  // placing the card below/above the WHOLE dropdown column (not just
  // this one item) instead: an above/below placement anchored to a
  // single item risks overlapping the sibling row right next to it,
  // exactly the problem side-placement exists to avoid in the first
  // place -- using the container's own bounds instead keeps that
  // guarantee even when side-placement itself isn't possible. The
  // spotlight ring still makes it obvious which row the card is about,
  // even though the card no longer sits flush against it.
  const bounds = dropdownRect || rect
  const left = clamp(bounds.left, MARGIN, vw - width - MARGIN)
  const spaceBelow = vh - bounds.bottom
  const spaceAbove = bounds.top
  if (spaceBelow >= CARD_HEIGHT_ESTIMATE + MARGIN) {
    return { ...base, left, top: bounds.bottom + MARGIN }
  }
  if (spaceAbove >= CARD_HEIGHT_ESTIMATE + MARGIN) {
    return { ...base, left, top: Math.max(bounds.top - CARD_HEIGHT_ESTIMATE - MARGIN, MARGIN) }
  }
  // No room above or below the container either (a very short window) --
  // nothing left to offset against; center vertically as a last resort.
  return { ...base, left, top: clamp(vh / 2 - CARD_HEIGHT_ESTIMATE / 2, MARGIN, vh - CARD_HEIGHT_ESTIMATE - MARGIN) }
}

function clamp(v, lo, hi){
  return Math.min(Math.max(v, lo), Math.max(lo, hi))
}
