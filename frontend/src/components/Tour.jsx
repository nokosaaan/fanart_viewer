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
      if (!step.targetId) { setRect(null); return }
      const el = document.querySelector(`[data-tour="${step.targetId}"]`)
      if (el) {
        setRect(el.getBoundingClientRect())
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
      if (el) setRect(el.getBoundingClientRect())
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

      <div style={cardStyle(rect)}>
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

function cardStyle(rect){
  const base = {
    position: 'fixed', zIndex: 5001, width: CARD_WIDTH, maxWidth: '90vw',
    background: '#1e293b', border: '1px solid #334155', borderRadius: 10,
    padding: 18, boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
  }
  if (!rect || rect === 'not-found') {
    return { ...base, left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }
  }
  // clientWidth/clientHeight (excludes any scrollbar) rather than
  // window.innerWidth/innerHeight (includes it) — the more conservative
  // of the two, so the card never ends up partly behind a scrollbar.
  const vw = document.documentElement.clientWidth || window.innerWidth
  const vh = document.documentElement.clientHeight || window.innerHeight

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

  if (spaceLeft >= CARD_WIDTH + MARGIN * 2) {
    return { ...base, left: rect.left - CARD_WIDTH - MARGIN, top: centeredTop }
  }
  if (spaceRight >= CARD_WIDTH + MARGIN * 2) {
    return { ...base, left: rect.right + MARGIN, top: centeredTop }
  }

  // Neither side has room (e.g. the target itself spans most of the
  // width, like the search input) — fall back to above/below instead.
  const left = clamp(rect.left, MARGIN, vw - CARD_WIDTH - MARGIN)
  const spaceBelow = vh - rect.bottom
  if (spaceBelow > 220) return { ...base, left, top: rect.bottom + MARGIN }
  return { ...base, left, top: Math.max(rect.top - 220, MARGIN) }
}

function clamp(v, lo, hi){
  return Math.min(Math.max(v, lo), Math.max(lo, hi))
}
