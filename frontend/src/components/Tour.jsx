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
  const placement = computeCardPlacement(rect, dropdownRect)

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

      <div style={placement.style}>
        {placement.arrow && <div style={arrowStyle(placement.arrow)} />}
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
const CARD_HEIGHT_ESTIMATE = 200 // rough; only used to keep the card on-screen vertically, not for layout
const MARGIN = 20
const ARROW_SIZE = 9 // speech-bubble tail (see arrowStyle) — MARGIN already leaves it room

const CARD_BASE = {
  position: 'fixed', zIndex: 5001,
  background: '#1e293b', border: '1px solid #334155', borderRadius: 10,
  padding: 18, boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
}

// Speech-bubble placement: figures out both the card's position AND, when
// there's a real spotlighted target, a pointer ("tail") on whichever edge
// faces it -- a plain rectangle sitting somewhere near the target left it
// ambiguous exactly which of several nearby controls a step was actually
// about (see HeaderMenu.jsx's tightly-packed dropdown items); an explicit
// arrow removes that ambiguity regardless of which side the card ends up
// on. Returns `{ style, arrow: {side, offset} | null }` -- `arrow` is null
// for the no-target (centered) card, and also as a last resort below when
// no side has enough room to avoid the card overlapping the target itself.
function computeCardPlacement(rect, dropdownRect){
  if (!rect || rect === 'not-found') {
    return {
      style: { ...CARD_BASE, width: CARD_WIDTH, maxWidth: '90vw',
        left: '50%', top: '50%', transform: 'translate(-50%, -50%)' },
      arrow: null,
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
  const style = { ...CARD_BASE, width, maxWidth: '90vw' }

  // Prefer placing the card beside the target (right, then left),
  // vertically centered on it — side placement is what actually avoids
  // two real problems a plain above/below placement had: a target near
  // the right edge (the header menu toggle, or any item inside its
  // dropdown) pushed the card off-screen or forced it to overlap the
  // target itself, and a target packed closely among siblings in a tall,
  // narrow list (the dropdown itself) made an above/below card overlap
  // the sibling right next to it.
  const spaceLeft = rect.left
  const spaceRight = vw - rect.right
  const centeredTop = clamp(
    rect.top + rect.height / 2 - CARD_HEIGHT_ESTIMATE / 2,
    MARGIN, vh - CARD_HEIGHT_ESTIMATE - MARGIN,
  )
  // Arrow offset (distance down from the card's own top edge) that points
  // at the target's vertical center, clamped so the tail never renders
  // outside the card's own edge even when the target sits far above/below
  // where centeredTop had to clamp the card to stay on-screen.
  const sideArrowOffset = clamp(
    rect.top + rect.height / 2 - centeredTop, ARROW_SIZE * 2, CARD_HEIGHT_ESTIMATE - ARROW_SIZE * 2,
  )

  if (spaceRight >= width + MARGIN * 2) {
    return {
      style: { ...style, left: rect.right + MARGIN, top: centeredTop },
      arrow: { side: 'left', offset: sideArrowOffset }, // tail on the card's LEFT edge, pointing left at the target
    }
  }
  if (spaceLeft >= width + MARGIN * 2) {
    return {
      style: { ...style, left: rect.left - width - MARGIN, top: centeredTop },
      arrow: { side: 'right', offset: sideArrowOffset }, // tail on the card's RIGHT edge, pointing right at the target
    }
  }

  // Neither side has room -- typically means the window itself is
  // narrower than usual (not maximized), not just that this particular
  // target is wide. If the target lives inside a dropdown, clear against
  // the WHOLE dropdown column (not just this one item) instead: an
  // above/below placement anchored to a single item risks overlapping the
  // sibling row right next to it, exactly the problem side-placement
  // exists to avoid in the first place -- using the container's own
  // bounds instead keeps that guarantee even when side-placement itself
  // isn't possible.
  const clearance = dropdownRect || rect
  const left = clamp(clearance.left, MARGIN, vw - width - MARGIN)
  const spaceBelow = vh - clearance.bottom
  const spaceAbove = clearance.top
  // Arrow offset (distance right from the card's own left edge) pointing
  // at the ACTUAL target's horizontal center (not the whole dropdown's),
  // clamped to stay within the card's own width.
  const belowAboveArrowOffset = clamp(rect.left + rect.width / 2 - left, ARROW_SIZE * 2, width - ARROW_SIZE * 2)

  if (spaceBelow >= CARD_HEIGHT_ESTIMATE + MARGIN) {
    return {
      style: { ...style, left, top: clearance.bottom + MARGIN },
      arrow: { side: 'top', offset: belowAboveArrowOffset }, // tail on the card's TOP edge, pointing up at the target
    }
  }
  if (spaceAbove >= CARD_HEIGHT_ESTIMATE + MARGIN) {
    return {
      style: { ...style, left, top: Math.max(clearance.top - CARD_HEIGHT_ESTIMATE - MARGIN, MARGIN) },
      arrow: { side: 'bottom', offset: belowAboveArrowOffset }, // tail on the card's BOTTOM edge, pointing down at the target
    }
  }

  // Nothing fits cleanly (a very short/narrow window) -- centering the
  // card WITHOUT a tail is deliberately preferred here over forcing any
  // of the placements above: every one of them would put the card
  // overlapping the very thing it's supposed to explain, which is worse
  // than just not pointing at it. The spotlight ring still highlights the
  // target on its own regardless.
  return {
    style: {
      ...style,
      left: clamp(vw / 2 - width / 2, MARGIN, vw - width - MARGIN),
      top: clamp(vh / 2 - CARD_HEIGHT_ESTIMATE / 2, MARGIN, vh - CARD_HEIGHT_ESTIMATE - MARGIN),
    },
    arrow: null,
  }
}

// Renders `arrow` (see computeCardPlacement) as a small solid CSS triangle
// on the given edge of the card, offset along that edge toward the
// target. `side` names which edge of the CARD the tail sits on (matching
// the direction it points, e.g. 'left' = tail on the card's left edge,
// pointing further left at the target).
function arrowStyle(arrow){
  const S = ARROW_SIZE
  const base = { position: 'absolute', width: 0, height: 0 }
  switch (arrow.side) {
    case 'left':
      return { ...base, left: -S, top: arrow.offset - S,
        borderTop: `${S}px solid transparent`, borderBottom: `${S}px solid transparent`,
        borderRight: `${S}px solid #1e293b` }
    case 'right':
      return { ...base, right: -S, top: arrow.offset - S,
        borderTop: `${S}px solid transparent`, borderBottom: `${S}px solid transparent`,
        borderLeft: `${S}px solid #1e293b` }
    case 'top':
      return { ...base, top: -S, left: arrow.offset - S,
        borderLeft: `${S}px solid transparent`, borderRight: `${S}px solid transparent`,
        borderBottom: `${S}px solid #1e293b` }
    case 'bottom':
    default:
      return { ...base, bottom: -S, left: arrow.offset - S,
        borderLeft: `${S}px solid transparent`, borderRight: `${S}px solid transparent`,
        borderTop: `${S}px solid #1e293b` }
  }
}

function clamp(v, lo, hi){
  return Math.min(Math.max(v, lo), Math.max(lo, hi))
}
