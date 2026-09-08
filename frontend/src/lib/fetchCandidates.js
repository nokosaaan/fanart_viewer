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

// Shared by ScrollList (single-item "+" button) and FetchQueueManager
// (page-level bulk fetch button) — both need to trigger the same
// preview-only candidate fetch against the backend.
// `options.signal`: an AbortSignal so a caller running many of these in a
// loop (FetchQueueManager's runBulkFetch) can cut a still-in-flight
// request short the moment the panel is closed, instead of it running to
// completion in the background regardless.
export async function fetchPreviewCandidates(id, url, options = {}){
  try{
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
