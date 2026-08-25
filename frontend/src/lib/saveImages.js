// Cloudflare (and most reverse proxies) cap request body size around 100MB.
// Several full-res images as base64 in one JSON payload can blow past that,
// so split into chunks and upload sequentially, keeping order consistent
// across chunks via start_index (only the first chunk clears old previews).
const MAX_BATCH_CHARS = 60 * 1024 * 1024

export async function saveImagesChunked(itemId, images){
  const batches = []
  let current = []
  let currentSize = 0
  for(const img of images){
    const size = (img.data_uri || '').length
    if(current.length > 0 && currentSize + size > MAX_BATCH_CHARS){
      batches.push(current)
      current = []
      currentSize = 0
    }
    current.push(img)
    currentSize += size
  }
  if(current.length > 0) batches.push(current)
  if(batches.length === 0) return { ok:false, body:{ detail:'No images provided' } }

  let startIndex = 0
  let totalSaved = 0
  for(let i=0; i<batches.length; i++){
    const batch = batches[i]
    const resp = await fetch(`/api/items/${itemId}/save_previews/`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ images: batch, clear_existing: i===0, start_index: startIndex }),
    })
    const j = await resp.json().catch(()=>({}))
    if(!resp.ok) return { ok:false, body:j }
    totalSaved += j.count || 0
    startIndex += batch.length
  }
  return { ok:true, body:{ status:'saved', count: totalSaved } }
}
