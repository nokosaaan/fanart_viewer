// Cross-window sync for the edit/fetch queue "pop out into a separate
// window" feature. Same-origin windows (main tab + any popped-out queue
// window) already share cookies/localStorage (so auth just works), but
// React state doesn't cross a window boundary — BroadcastChannel is the
// piece that lets them stay live-synced instead of needing a manual reload.
//
// Falls back to a no-op if BroadcastChannel isn't available (very old
// browsers) — sync features degrade gracefully; the windows just stop
// mirroring each other, they don't error out.
const CHANNEL_NAME = 'fanart-viewer-sync'

let channel = null
function getChannel(){
  if(channel === null){
    channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(CHANNEL_NAME) : false
  }
  return channel || null
}

export function postSync(type, payload){
  const ch = getChannel()
  if(ch) ch.postMessage({ type, payload })
}

// Returns an unsubscribe function.
export function onSync(type, handler){
  const ch = getChannel()
  if(!ch) return () => {}
  const listener = (ev) => { if(ev.data && ev.data.type === type) handler(ev.data.payload) }
  ch.addEventListener('message', listener)
  return () => ch.removeEventListener('message', listener)
}

// Fires a same-window CustomEvent (as before — every existing listener like
// ScrollList's ItemRow or App.jsx's item-updated handler keeps working
// unmodified) AND broadcasts it cross-window. main.jsx bridges incoming
// broadcasts back into local CustomEvents (see onSync calls there), so
// every consumer becomes cross-window-aware without being touched — only
// the origination points (this function) needed to change.
export function notify(type, detail){
  try{ window.dispatchEvent(new CustomEvent(type, { detail })) }catch(_){}
  postSync(type, detail)
}
