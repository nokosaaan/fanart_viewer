const STORAGE_KEY = 'fv_custom_situations'
export const DEFAULT_SITUATIONS = ['SOLO', 'CP', 'MULTIPLE', 'PARODY', 'R18']

export function getCustomSituations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch (e) {
    return []
  }
}

export function saveCustomSituation(newSit) {
  const val = (newSit || '').trim().toUpperCase()
  if (!val) return getCustomSituations()
  const existing = getCustomSituations()
  if (!existing.includes(val) && !DEFAULT_SITUATIONS.includes(val)) {
    const next = [...existing, val]
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      window.dispatchEvent(new CustomEvent('fv_custom_situations_updated', { detail: next }))
    } catch (e) {}
    return next
  }
  return existing
}

export function getAllSituations(items = []) {
  const base = new Set(DEFAULT_SITUATIONS)
  const custom = getCustomSituations()
  for (const c of custom) {
    if (c) base.add(c.toUpperCase())
  }
  if (Array.isArray(items)) {
    for (const it of items) {
      const s = (it && it.situation ? String(it.situation) : '').trim().toUpperCase()
      if (s) base.add(s)
    }
  }
  return Array.from(base)
}
