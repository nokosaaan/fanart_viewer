// Delay to insert between consecutive fetchPreviewCandidates calls in any
// loop that fires several in a row (FetchQueueManager's bulk-fetch button,
// RetweetFetchManager/BookmarkFetchManager's queue-mode scans) — each call
// hits fetch_and_save_preview's per-tweet TweetDetail GraphQL request,
// which this app has already confirmed in practice triggers Twitter's rate
// limit after a burst of requests with no spacing (see
// twitter_gql_fetch.fetch_account_retweets' own docstring: "一度に40件
// fetchするとレート制限で数分search等が使えなくなる"). A single request on
// its own works fine; only back-to-back-with-no-gap loops were ever
// observed to fail — reported as "bulk fetch fails on every item, but the
// same item fetched individually right after succeeds".
export const BULK_FETCH_DELAY_MS = 2000

export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// How many finished files are grouped into one fetch-queue entry when a
// background gallery-dl job streams its results (see fetchViaGalleryDlJob).
const GALLERYDL_QUEUE_BATCH = 20
const GALLERYDL_POLL_INTERVAL_MS = 2000

function fireAndForgetCancel(jobId){
  try{
    fetch('/api/items/gallerydl_cancel/', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({job_id: jobId}), keepalive: true}).catch(()=>{})
  }catch(_){}
}

// gallery-dl downloads can take many minutes for a multi-page gallery (sites
// throttle requests), so this runs as a background job on the server and
// polls for files as they finish instead of holding one request open.
//   options.onBatch(images): when given, finished files are handed over in
//     groups of GALLERYDL_QUEUE_BATCH as they arrive (the caller enqueues
//     them), and the returned body has `images: []` plus `streamed: <count>`.
//     Without it, everything is collected and returned in `images` at the end.
//   options.onProgress({ready, status}): called after every poll.
async function fetchViaGalleryDlJob(id, url, options = {}){
  const startResp = await fetch(`/api/items/${id}/gallerydl_start/`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(url ? {url} : {}), signal: options.signal})
  if(!startResp.ok){
    return { ok: false, body: await startResp.json().catch(()=>({})) }
  }
  const started = await startResp.json().catch(()=>({}))
  const jobId = started.job_id
  const jobUrl = started.url || url || ''
  if(!jobId) return { ok: false, body: { detail: 'gallery-dl job could not be started' } }

  let since = 0
  let streamed = 0
  let pending = []
  const collected = []
  const flush = (force) => {
    while(pending.length >= GALLERYDL_QUEUE_BATCH || (force && pending.length > 0)){
      const chunk = pending.splice(0, GALLERYDL_QUEUE_BATCH)
      if(options.onBatch){ options.onBatch(chunk); streamed += chunk.length }
      else collected.push(...chunk)
    }
  }

  try{
    while(true){
      if(options.signal && options.signal.aborted){
        const err = new Error('aborted'); err.name = 'AbortError'; throw err
      }
      const q = new URLSearchParams({ job_id: jobId, since: String(since), url: jobUrl, item_id: String(id) })
      const r = await fetch(`/api/items/gallerydl_poll/?${q.toString()}`, { signal: options.signal })
      if(!r.ok){
        flush(true)
        return { ok: false, body: await r.json().catch(()=>({})) }
      }
      const j = await r.json().catch(()=>({}))
      since = typeof j.next === 'number' ? j.next : since
      if(Array.isArray(j.images) && j.images.length) pending.push(...j.images)
      flush(false)
      if(options.onProgress) options.onProgress({ ready: j.total_ready || 0, status: j.status })
      if(j.drained){
        flush(true)
        if(streamed === 0 && collected.length === 0){
          return { ok: false, body: { detail: j.error || 'No image candidates found or failed to fetch' } }
        }
        break
      }
      // Files are already waiting: ask for the next batch right away.
      if(!(Array.isArray(j.images) && j.images.length > 0 && since < (j.total_ready || 0))){
        await sleep(GALLERYDL_POLL_INTERVAL_MS)
      }
    }
  }catch(e){
    fireAndForgetCancel(jobId)
    throw e
  }
  if(options.onBatch) return { ok: true, body: { preview_only: true, images: [], streamed, method: 'gallerydl' } }
  return { ok: true, body: { preview_only: true, images: collected, method: 'gallerydl' } }
}

// Shared by ScrollList (single-item "+" button) and FetchQueueManager
// (page-level bulk fetch button) — both need to trigger the same
// preview-only candidate fetch against the backend.
// `options.signal`: an AbortSignal so a caller running many of these in a
// loop (FetchQueueManager's runBulkFetch) can cut a still-in-flight
// request short the moment the panel is closed, instead of it running to
// completion in the background regardless.
export async function fetchPreviewCandidates(id, url, options = {}){
  try{
    if(options.force_method === 'gallerydl'){
      return await fetchViaGalleryDlJob(id, url, options)
    }
    const body = {}
    if(url) body.url = url
    body.preview_only = true
    // only include force_method when explicitly requested by the UI
    if(options.force_method) body.force_method = options.force_method
    const resp = await fetch(`/api/items/${id}/fetch_and_save_preview/`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body), signal: options.signal})
    if(!resp.ok) {
      const j = await resp.json().catch(()=>({}));
      return { ok: false, body: j }
    }
    return { ok: true, body: await resp.json().catch(()=>({})) }
  }catch(e){
    if(e && e.name === 'AbortError') throw e  // let the caller's own cancellation check handle this, not a real failure
    console.error(e); return { ok: false, body: {error: e.message} }
  }
}
