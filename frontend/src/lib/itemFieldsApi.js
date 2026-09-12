function getCookie(name) {
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

// Shared by ItemEditForm.save() and ItemQueuePanel's own combined save
// orchestration — a single implementation instead of two copies that could
// silently drift apart. `payload`: {titles, characters, situation, tags, artist}.
export async function saveItemFields(itemId, payload) {
  const resp = await fetch(`/api/items/${itemId}/update_fields/`, {
    method: 'POST',
    headers: HEADERS,
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  })
  const j = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(j.detail || JSON.stringify(j))
  return j
}

// Shared by RegionAnnotator.save() and ItemQueuePanel's own combined save
// orchestration. `regions`: [{image_index, box:[x1,y1,x2,y2], characters:[...]}, ...]
// (already filtered to labeled-only boxes by the caller — see
// RegionAnnotator.getPayload()/save()).
export async function saveCharacterRegions(itemId, regions) {
  const resp = await fetch(`/api/items/${itemId}/character_regions/`, {
    method: 'POST',
    headers: HEADERS,
    credentials: 'same-origin',
    body: JSON.stringify({ regions }),
  })
  const j = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(j.detail || `保存に失敗しました (${resp.status})`)
  return j
}
