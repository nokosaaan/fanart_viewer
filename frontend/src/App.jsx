import React, {useEffect, useState, useMemo, useRef} from 'react'
import SearchBar from './components/SearchBar'
import ScrollList from './components/ScrollList'
import PreviewPane from './components/PreviewPane'
import LoginScreen from './components/LoginScreen'
import CharacterGroupManager from './components/CharacterGroupManager'
import CharacterAliasGroupManager from './components/CharacterAliasGroupManager'
import CharacterDanbooruLinkManager from './components/CharacterDanbooruLinkManager'
import BackupManager from './components/BackupManager'
import TrainClassifierManager from './components/TrainClassifierManager'
import FetchQueueManager from './components/FetchQueueManager'
import EditQueueManager from './components/EditQueueManager'
import RegionLabelQueueManager from './components/RegionLabelQueueManager'
import ManualAddItem from './components/ManualAddItem'
import EditFields from './components/EditFields'
import TwitterFetchManager from './components/TwitterFetchManager'
import TwitterCredsManager from './components/TwitterCredsManager'
import PixivCredsManager from './components/PixivCredsManager'
import PoipikuCredsManager from './components/PoipikuCredsManager'
import HeaderMenu from './components/HeaderMenu'
import Pagination from './components/Pagination'
import Tour from './components/Tour'
import { loadCachedItems, saveCachedItems } from './lib/itemsCache'
import { notify } from './lib/crossWindowSync'
import { fetchPreviewCandidates, sleep, BULK_FETCH_DELAY_MS } from './lib/fetchCandidates'
import { ReloadIcon, FetchQueueIcon, EditQueueIcon, RegionQueueIcon, SwipeIcon, BackupIcon, BrainGearIcon } from './components/MenuIcons'
import { buildTourStepsA, buildTourStepsB } from './lib/tourSteps'

// Platform badge/icon + text for a header-menu label — see HeaderMenu.jsx's
// MenuEntry, which renders `label` as-is (plain string or JSX both work).
// `icon`: an image src (platform badges — twitter.svg etc). `iconNode`: an
// arbitrary node instead (MenuIcons.jsx's inline SVGs, or a plain emoji
// string) for entries with no dedicated image asset.
function MenuIconLabel({ icon, iconNode, text }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {icon && <img src={icon} alt="" style={{ width: 16, height: 16, borderRadius: 3 }} />}
      {iconNode && (
        <span style={{ width: 18, height: 18, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 14, lineHeight: 1 }}>
          {iconNode}
        </span>
      )}
      {text}
    </span>
  )
}

function AppMain({ role, onLogout }){
  const readOnly = role === 'viewer'

  // Lazy-initialize from whatever was cached last session so the list paints
  // immediately on reload instead of sitting empty until the network fetch
  // below resolves. The mount effect still fetches fresh data right away and
  // merges it in, so this is purely a "show something now" optimization —
  // never the final source of truth.
  const [items, setItems] = useState(() => loadCachedItems() || [])
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState([])
  const [includeCP, setIncludeCP] = useState(false)
  const [includeR18, setIncludeR18] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  // Set by ScrollList's preview-thumbnail click (see openPreviewForItem) so
  // PreviewPane opens jumped straight to that item instead of the plain
  // timeline grid. Cleared whenever the pane closes so a later reopen via
  // the header menu (not tied to any specific item) doesn't re-jump to a
  // stale target.
  const [previewInitialItemId, setPreviewInitialItemId] = useState(null)
  function openPreviewForItem(itemId){
    setPreviewInitialItemId(itemId)
    setPreviewOpen(true)
  }
  function closePreview(){
    setPreviewOpen(false)
    setPreviewInitialItemId(null)
  }
  // editQueueOpen/regionQueueOpen only ever control visibility, not
  // whether the component is mounted at all (see editQueueMounted/
  // regionQueueMounted below) — closing either queue used to fully unmount
  // it, throwing away everything (which item was selected, any characters
  // typed into ItemEditForm but not yet saved) the moment you closed it to
  // go check something else, like the original source link, and forcing a
  // separate popped-out window to become the only way to avoid that. Once
  // opened, the panel now just gets hidden on close and keeps its state for
  // the rest of the session, so "close briefly, come back, keep going" no
  // longer needs a whole other window.
  const [editQueueOpen, setEditQueueOpen] = useState(false)
  const [editQueueMounted, setEditQueueMounted] = useState(false)
  const [regionQueueOpen, setRegionQueueOpen] = useState(false)
  const [regionQueueMounted, setRegionQueueMounted] = useState(false)
  const [charGroupOpen, setCharGroupOpen] = useState(false)
  const [charAliasGroupOpen, setCharAliasGroupOpen] = useState(false)
  const [charLinkOpen, setCharLinkOpen] = useState(false)
  const [backupOpen, setBackupOpen] = useState(false)
  const [trainClassifierOpen, setTrainClassifierOpen] = useState(false)
  // Onboarding tour (see Tour.jsx / lib/tourSteps.js) — needs the header
  // menu forced open across several steps (most tour targets are menu
  // items), which plain internal HeaderMenu state can't support from out
  // here, hence lifting this one piece of state up.
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false)
  const [tourActive, setTourActive] = useState(null) // null | 'A' | 'B'
  // 'welcome' | 'part2' | null — a plain confirm-style gate shown BEFORE
  // starting a tour automatically, so someone who wants none of this can
  // say so once and never be asked again (re-triggering later is still
  // always available via the header menu's own 💡 entry).
  const [tourPrompt, setTourPrompt] = useState(null)

  function startTour(group){
    // Whether the menu should be open is decided per-step from here on
    // (see Tour.jsx's onMenuNeed) — the very first step in both groups
    // doesn't target anything inside it, so forcing it open here would
    // just have Tour immediately close it again a moment later.
    setTourActive(group)
  }
  function closeTour(){
    setTourActive(null)
    setHeaderMenuOpen(false)
  }

  // First-run gate: ask once, remember the answer either way (declining
  // is itself a real answer, not "ask me again next launch").
  useEffect(() => {
    if (readOnly) return
    try {
      if (!localStorage.getItem('fv_tour_intro_shown')) {
        localStorage.setItem('fv_tour_intro_shown', '1')
        setTourPrompt('welcome')
      }
    } catch (_) {}
  }, [readOnly])

  // Once the archive has grown past a handful of items, the Act4+ tour
  // (search/AI/backup) becomes relevant in a way it just isn't for an
  // empty or near-empty library on day one — offered once, same
  // ask-once-remember-the-answer rule as the welcome gate above.
  useEffect(() => {
    if (readOnly || tourPrompt || tourActive) return
    if (!Array.isArray(items) || items.length < 10) return
    try {
      if (!localStorage.getItem('fv_tour_part2_shown')) {
        localStorage.setItem('fv_tour_part2_shown', '1')
        setTourPrompt('part2')
      }
    } catch (_) {}
  }, [items, readOnly, tourPrompt, tourActive])
  // Mailbox-style queue: fetching an item's image candidates (ScrollList)
  // appends here instead of popping an inline modal, so accidentally
  // clicking outside a modal backdrop can no longer discard results that
  // would otherwise require re-fetching. Reviewed/processed from the header
  // "取得キュー" button (FetchQueueManager). In-memory only — cleared on reload.
  const [fetchQueue, setFetchQueue] = useState([])
  const [fetchQueueOpen, setFetchQueueOpen] = useState(false)
  const [manualAddOpen, setManualAddOpen] = useState(false)
  // The item ManualAddItem just created — opened straight into the normal
  // edit form (titles/characters/tags/situation are empty at creation
  // time on purpose, same as any other freshly-fetched item) instead of
  // asking for that metadata in the upload dialog itself.
  const [pendingNewItem, setPendingNewItem] = useState(null)
  function handleItemCreated(item){
    setItems(prev => [item, ...(Array.isArray(prev) ? prev : [])])
    setManualAddOpen(false)
    setPendingNewItem(item)
  }
  const [twitterFetchOpen, setTwitterFetchOpen] = useState(false)
  const [twitterCredsOpen, setTwitterCredsOpen] = useState(false)
  const [pixivCredsOpen, setPixivCredsOpen] = useState(false)
  const [poipikuCredsOpen, setPoipikuCredsOpen] = useState(false)
  // Opens a queue manager as its own browser window (same origin, so
  // cookies/localStorage — and thus auth — carry over automatically) instead
  // of an overlay in this one, so the two can sit side by side. See
  // StandaloneQueueWindow below and the panel=... check in App() for the
  // other end of this.
  function openStandaloneWindow(panel){
    window.open(`?panel=${panel}`, `fv-${panel}`, 'width=1100,height=760')
  }
  // Popping a queue out into its own window loses this window's in-memory
  // state (window.open with a `panel=...` query string is a fresh document
  // load, not a shared JS realm) — the popped window used to have no idea
  // which page you'd been reviewing and fell all the way back to scanning
  // the entire DB. Handing off a snapshot of exactly what this window was
  // showing (allItems/pageSize/initialPage) via localStorage — read back once
  // by the `panel=...` branch below and immediately removed — keeps the new
  // window scoped to the same page instead. localStorage can in principle
  // throw (quota, private-browsing) for a very large library; if so, still
  // open the window rather than blocking the pop-out — it just falls back to
  // the server-wide queue on that end, same as if nothing had been handed off.
  function popOutQueue(panel, allItems, pageSize, initialPage){
    try{
      localStorage.setItem(`fv-queue-handoff-${panel}`, JSON.stringify({ allItems, pageSize, initialPage }))
    }catch(e){
      console.error('Failed to hand off queue page to standalone window', e)
    }
    openStandaloneWindow(panel)
  }
  function enqueueFetchResult({ itemId, images }){
    setFetchQueue(prev => [...prev, { id: `${itemId}-${Date.now()}-${Math.random().toString(36).slice(2,7)}`, itemId, images, fetchedAt: Date.now() }])
  }
  function removeFromFetchQueue(entryId){
    setFetchQueue(prev => prev.filter(e => e.id !== entryId))
  }
  // fetchQueue only ever lives here (App.jsx) — it's ephemeral/in-memory,
  // there's no server copy to independently re-fetch. FetchQueueManager is
  // always rendered as an overlay in this same window now (no more
  // popped-out standalone window — see fetchQueueOpen above), so it reads
  // this state directly via props; no cross-window mirroring needed.

  // Bulk-fetch run state lives HERE (App.jsx), not inside FetchQueueManager
  // itself, specifically so closing that overlay (fetchQueueOpen -> false,
  // unmounting the panel) does NOT stop a bulk fetch already in progress —
  // App.jsx stays mounted for the whole session, so the loop just keeps
  // going in the background and you can reopen the panel later to see
  // where it landed (or watch it live via the header menu badge below).
  const [bulkFetchRunning, setBulkFetchRunning] = useState(false)
  const [bulkFetchProgress, setBulkFetchProgress] = useState(null) // {done, total}
  const [bulkFetchSummary, setBulkFetchSummary] = useState(null)
  const bulkFetchCancelledRef = useRef(false)
  const bulkFetchAbortRef = useRef(null)

  // Runs the exact same per-item fetch ScrollList's own "+" button does
  // (fetchPreviewCandidates, then onEnqueueFetch/notify on success) for
  // every item in `pendingItems`, one at a time — FetchQueueManager's
  // bulk-fetch button just automates clicking "+" down the page's list
  // instead of introducing any separate save/notify path of its own.
  // `pendingItems` is a snapshot taken at click time (see FetchQueueManager),
  // so subsequent pagination/filter changes while this runs don't retarget
  // an already-started run.
  async function runBulkFetch(pendingItems, forceMethod){
    if(bulkFetchRunning || !pendingItems || pendingItems.length === 0) return
    // Same mapping ScrollList.jsx's own per-item dropdown uses: 'html' is
    // this app's existing default cascade (direct-image URL, then HTML
    // og:image scrape), so only 'api'/'playwright' are ever passed through
    // as an explicit override.
    const forceMethodParam = forceMethod === 'api' ? 'api' : (forceMethod === 'playwright' ? 'playwright' : undefined)
    bulkFetchCancelledRef.current = false
    bulkFetchAbortRef.current = new AbortController()
    setBulkFetchRunning(true)
    setBulkFetchSummary(null)
    let queued = 0, savedDirect = 0, failed = 0
    for(let i=0; i<pendingItems.length; i++){
      if(bulkFetchCancelledRef.current) break
      // Space out requests — see BULK_FETCH_DELAY_MS's own comment: firing
      // these back-to-back with no gap has been observed to trip
      // Twitter's rate limit and fail every item in the batch.
      if(i > 0){
        await sleep(BULK_FETCH_DELAY_MS)
        if(bulkFetchCancelledRef.current) break
      }
      setBulkFetchProgress({ done: i, total: pendingItems.length })
      const it = pendingItems[i]
      try{
        const res = await fetchPreviewCandidates(it.id, it.link, { force_method: forceMethodParam, signal: bulkFetchAbortRef.current.signal })
        if(bulkFetchCancelledRef.current) break  // cancelled while this request was in flight — discard its result
        const body = res.body || {}
        if(res.ok && body.status === 'saved'){
          savedDirect++
          notify('item-preview-updated', { id: it.id })
        } else if(res.ok && body.preview_only && Array.isArray(body.images) && body.images.length > 0){
          enqueueFetchResult({ itemId: it.id, images: body.images })
          queued++
        } else {
          failed++
        }
      }catch(e){
        if(bulkFetchCancelledRef.current || (e && e.name === 'AbortError')) break
        console.error('Bulk fetch failed for item', it.id, e)
        failed++
      }
    }
    setBulkFetchProgress({ done: pendingItems.length, total: pendingItems.length })
    setBulkFetchRunning(false)
    setBulkFetchSummary(
      bulkFetchCancelledRef.current
        ? `キャンセルしました: キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件`
        : `完了: キューに${queued}件追加 / 直接保存${savedDirect}件 / 失敗${failed}件`
    )
  }

  function cancelBulkFetch(){
    bulkFetchCancelledRef.current = true
    if(bulkFetchAbortRef.current) bulkFetchAbortRef.current.abort()
  }
  const [situationFilter, setSituationFilter] = useState('ALL')
  const [titleMissingOnly, setTitleMissingOnly] = useState(false)
  const [previewMissingOnly, setPreviewMissingOnly] = useState(false)
  const [pageIndex, setPageIndex] = useState(0)
  const PAGE_SIZE = 50
  const [nextPageUrl, setNextPageUrl] = useState(null)
  const [loadingPages, setLoadingPages] = useState(false)
  const [backgroundIndexing, setBackgroundIndexing] = useState(false)
  // Kept in sync with nextPageUrl but readable synchronously mid-async-function,
  // so a loop of several loadNextPage() calls in a row (see goToNextPage) doesn't
  // keep re-reading the stale value captured when the loop started.
  const nextPageUrlRef = useRef(null)
  useEffect(()=>{ nextPageUrlRef.current = nextPageUrl }, [nextPageUrl])
  // Same idea for the raw loaded item count — used to decide how many more
  // backend pages a page-jump needs, without waiting on filtered/totalPages
  // (a memo, so it can't be read fresh mid-loop either).
  const itemsCountRef = useRef(items.length)
  // Guards the search-triggered full background load so it only ever starts once.
  const fullIndexStartedRef = useRef(false)
  const INITIAL_PAGES = 3

  // Fetch backend pages starting at `startUrl`, following `next` up to
  // `maxPages` times (Infinity = fetch everything). Shared by the initial
  // fast-path load, the on-demand full index, and the debug fetchAll().
  async function fetchItemsPages(startUrl, maxPages = Infinity){
    const collected = []
    let url = startUrl
    let pages = 0
    while(url && pages < maxPages){
      let fetchUrl = url
      try{
        if(typeof url === 'string' && (url.startsWith('http://') || url.startsWith('https://'))){
          const u = new URL(url)
          fetchUrl = u.pathname + (u.search || '')
        }
      }catch(_){ fetchUrl = url }

      const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
      if(!r.ok){
        let bodyText = null
        try{ bodyText = await r.text() }catch(_){ bodyText = null }
        console.error('fetch failed', fetchUrl, r.status, bodyText)
        break
      }
      let data = null
      try{ data = await r.json() }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        break
      }

      if(Array.isArray(data)){
        collected.push(...data)
        url = null
        break
      }
      const results = Array.isArray(data.results) ? data.results : []
      collected.push(...results)
      url = data.next || null
      pages += 1
    }
    return { items: collected, nextUrl: url }
  }

  // keep the original full-fetch routine available for debugging
  const fetchAll = async () => {
    try{
      const { items: all } = await fetchItemsPages('/api/items/')
      const unique = uniqueById(all)
      itemsCountRef.current = unique.length
      setItems(unique)
      setNextPageUrl(null)
    }catch(err){
      console.error('Failed to fetch items', err)
      itemsCountRef.current = 0
      setItems([])
      setNextPageUrl(null)
    }
  }

  useEffect(()=>{
    // Expose debug function on window for manual invocation in dev tools
    if(typeof window !== 'undefined'){
      window.fetchAllItems = fetchAll
    }

    // Load only the first few backend pages up front for a fast initial paint.
    // The rest is fetched lazily as the user pages forward (goToNextPage) or
    // once a search/filter needs the full dataset (see the effect below).
    // `items` may already hold last session's cached list at this point (see
    // the useState initializer above) — merge rather than overwrite so a slow
    // or failed fetch doesn't blank out something we already had to show.
    ;(async ()=>{
      try{
        const { items: initial, nextUrl } = await fetchItemsPages('/api/items/', INITIAL_PAGES)
        setItems(prev => {
          const merged = uniqueById([...initial, ...(Array.isArray(prev) ? prev : [])])
          itemsCountRef.current = merged.length
          return merged
        })
        setNextPageUrl(nextUrl)
      }catch(err){
        // Leave `items`/`nextPageUrl` as-is (whatever the cache restored, or
        // empty if there was none) rather than blanking the list on a
        // transient network error.
        console.error('Failed to fetch items — keeping cached list, if any', err)
      }
    })()
    // Listen for item-deleted events to remove items from local state
    function onItemDeleted(ev){
      try{
        const id = ev && ev.detail && ev.detail.id
        if(id == null) return
        setItems(prev => Array.isArray(prev) ? prev.filter(it=> it.id !== id && it.pk !== id && it.external_id !== id) : prev)
      }catch(e){/* ignore */}
    }
    window.addEventListener('item-deleted', onItemDeleted)
    // Merge an edited item (from EditFields) back into the shared items list.
    // Without this, only the editing row's own local display state updated —
    // the underlying item object here stayed stale, so reopening the editor
    // later showed pre-edit values and the user had to redo the whole edit.
    function onItemUpdated(ev){
      try{
        const updated = ev && ev.detail && ev.detail.item
        if(!updated || updated.id == null) return
        setItems(prev => Array.isArray(prev) ? prev.map(it => it.id === updated.id ? { ...it, ...updated } : it) : prev)
      }catch(e){/* ignore */}
    }
    window.addEventListener('item-updated', onItemUpdated)
    // Every preview mutation (manual upload, salvage, fetch-and-save, clear
    // one/all previews -- see ScrollList.jsx/PreviewPane.jsx) broadcasts
    // this with just an id, and used to only flip each individual row's own
    // local `hasPreviewLocal` state -- the shared `items` array here kept
    // whatever (possibly has_preview:false) value it had from its last
    // fetch. That stale `has_preview` then got written into the localStorage
    // cache (see the debounced saveCachedItems effect below) and seeded
    // right back in on the next full reload, silently overriding a preview
    // that had, in fact, been saved. Re-fetch just this one item and merge
    // it in, the same way onItemUpdated already does for edits.
    function onItemPreviewUpdated(ev){
      const id = ev && ev.detail && ev.detail.id
      if(id == null) return
      ;(async () => {
        try{
          const r = await fetch(`/api/items/${id}/`)
          if(!r.ok) return
          const fresh = await r.json()
          setItems(prev => Array.isArray(prev) ? prev.map(it => it.id === fresh.id ? { ...it, ...fresh } : it) : prev)
        }catch(e){ /* next full reload will pick up the correct state anyway */ }
      })()
    }
    window.addEventListener('item-preview-updated', onItemPreviewUpdated)
    return ()=>{
      window.removeEventListener('item-deleted', onItemDeleted)
      window.removeEventListener('item-updated', onItemUpdated)
      window.removeEventListener('item-preview-updated', onItemPreviewUpdated)
    }
  }, [])

  // Keep the on-disk cache in sync with whatever's loaded, so the next
  // reload can paint from it immediately (see the useState initializer
  // above). Debounced so rapid-fire updates (e.g. the background full-index
  // fetch appending page after page) don't serialize the whole list on every
  // single page.
  useEffect(()=>{
    const t = setTimeout(()=>{ saveCachedItems(items) }, 500)
    return ()=> clearTimeout(t)
  }, [items])

  // Search/filters only see whatever's been loaded so far. The first time the
  // user actually filters — a text query, a filter chip, OR any of the
  // quick toggles (situation/title-missing/preview-missing) — fetch the
  // rest of the dataset in the background (once) so results aren't
  // silently incomplete. situationFilter/titleMissingOnly/previewMissingOnly
  // used to be left out of this check entirely, so narrowing down to just
  // e.g. "SOLO" only ever filtered whatever pages happened to already be
  // loaded — anything past that point (not yet paged/scrolled into `items`)
  // silently never appeared as a match, in both the main list AND
  // PreviewPane (which intersects its own broader fetch against this same
  // `filtered` array's id set — see App.jsx's <PreviewPane filteredItems=...>
  // and PreviewPane.jsx's own loadItems/effect), which is exactly what made
  // Preview Timeline look like it wasn't honoring the current filter.
  useEffect(()=>{
    const searching = query.trim() !== '' || filters.length > 0 ||
      situationFilter !== 'ALL' || titleMissingOnly || previewMissingOnly
    if(!searching || !nextPageUrl || fullIndexStartedRef.current) return
    fullIndexStartedRef.current = true
    let cancelled = false
    ;(async ()=>{
      setBackgroundIndexing(true)
      try{
        const { items: rest, nextUrl } = await fetchItemsPages(nextPageUrl)
        if(cancelled) return
        setItems(prev => {
          // Freshly-fetched first (see the initial-load effect above for the
          // same ordering, and why) -- a stale cached `prev` entry for an id
          // that also appears in `rest` must not win over what was just
          // re-fetched from the server.
          const merged = uniqueById([...rest, ...(Array.isArray(prev)?prev:[])])
          itemsCountRef.current = merged.length
          return merged
        })
        nextPageUrlRef.current = nextUrl
        setNextPageUrl(nextUrl)
      }catch(err){
        console.error('Background indexing failed', err)
      }finally{
        if(!cancelled) setBackgroundIndexing(false)
      }
    })()
    return ()=>{ cancelled = true }
  }, [query, filters, situationFilter, titleMissingOnly, previewMissingOnly, nextPageUrl])

  const suggestions = useMemo(()=>{
    const set = new Set()
    const list = Array.isArray(items) ? items : (items && Array.isArray(items.results) ? items.results : [])
    list.forEach(it=>{
      if(Array.isArray(it.titles)) {
        it.titles.forEach(t=> set.add(t))
      } else if(typeof it.titles === 'string' && it.titles) {
        set.add(it.titles)
      }

      if(Array.isArray(it.characters)) {
        it.characters.forEach(c=> set.add(c))
      } else if(typeof it.characters === 'string' && it.characters) {
        set.add(it.characters)
      }

      if(Array.isArray(it.tags)) {
        it.tags.forEach(tag=> set.add(tag))
      } else if(typeof it.tags === 'string' && it.tags) {
        set.add(it.tags)
      }
    })
    return Array.from(set)
  }, [items])

  const filtered = useMemo(()=>{
    const q = query.trim().toLowerCase()
    const list = Array.isArray(items) ? items : (items && Array.isArray(items.results) ? items.results : [])

    function hasAnyTitle(it){
      if(!it) return false
      if(Array.isArray(it.titles)){
        return it.titles.some(t => String(t || '').trim().length > 0)
      }
      if(typeof it.titles === 'string'){
        return it.titles.trim().length > 0
      }
      return false
    }

    return list.filter(it=>{
      if(!includeCP && (it.situation||'').toUpperCase()==='CP') return false
      if((readOnly || !includeR18) && (it.situation||'').toUpperCase()==='R18') return false
      if(situationFilter && situationFilter!=='ALL'){
        if(((it.situation||'').toUpperCase()) !== situationFilter) return false
      }
      if(titleMissingOnly && hasAnyTitle(it)) return false
      // has_preview comes straight off ItemSerializer (see backend/item/
      // serializers.py) -- no extra request needed, this is just a filter
      // over data already loaded with the item.
      if(previewMissingOnly && (it.has_preview === true || it.has_preview === 'true')) return false
      if(filters.length===0 && q==='') return true
      const hay = [ ...(it.titles||[]), ...(it.characters||[]), ...(it.tags||[]), it.artist, it.link ].join(' ').toLowerCase()
      const matchesQuery = q==='' || hay.includes(q)
      const matchesFilters = filters.every(f => hay.includes(f.toLowerCase()))
      return matchesQuery && matchesFilters
    })
  }, [items, query, filters, includeCP, includeR18, situationFilter, titleMissingOnly, previewMissingOnly, readOnly])


  // pagination over filtered results
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(()=>{
    // reset to first page if filters change
    setPageIndex(0)
  }, [query, filters, includeCP, includeR18, situationFilter, titleMissingOnly, previewMissingOnly])

  const paginatedItems = useMemo(()=>{
    const start = pageIndex * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, pageIndex])

  function addFilter(value){
    if(!value) return
    setFilters(prev=> prev.includes(value)? prev : [...prev, value])
    setQuery('')
  }

  function removeFilter(value){
    setFilters(prev=> prev.filter(p=>p!==value))
  }

  // Returns true if a page was actually fetched. Reads/writes nextPageUrlRef
  // (not just the nextPageUrl state) so a caller can await this in a loop —
  // e.g. goToNextPage() below — and see the updated url on the next iteration
  // instead of the value from whenever the loop started.
  async function loadNextPage(){
    const url = nextPageUrlRef.current
    if(!url || loadingPages) return false
    setLoadingPages(true)
    try{
      let fetchUrl = url
      try{
        if(typeof fetchUrl === 'string' && (fetchUrl.startsWith('http://') || fetchUrl.startsWith('https://'))){
          const u = new URL(fetchUrl)
          fetchUrl = u.pathname + (u.search || '')
        }
      }catch(_){ /* leave fetchUrl as-is */ }

      const r = await fetch(fetchUrl, { headers: { 'Accept': 'application/json' } })
      if(!r.ok){
        let bodyText = null
        try{ bodyText = await r.text() }catch(_){ bodyText = null }
        console.error('fetch failed', fetchUrl, r.status, bodyText)
        return false
      }
      let data = null
      try{ data = await r.json() }catch(err){
        let raw = null
        try{ raw = await r.text() }catch(_){ raw = null }
        console.error('Invalid JSON from', fetchUrl, 'error:', err, 'body:', raw)
        return false
      }

      const results = Array.isArray(data) ? data : (Array.isArray(data.results) ? data.results : [])
      const newNext = Array.isArray(data) ? null : (data.next || null)
      setItems(prev => {
        const merged = uniqueById([...(Array.isArray(prev)?prev:[]), ...results])
        itemsCountRef.current = merged.length
        return merged
      })
      nextPageUrlRef.current = newNext
      setNextPageUrl(newNext)
      return true
    }catch(err){
      console.error('Failed to load next page', err)
      return false
    }finally{
      setLoadingPages(false)
    }
  }

  // Fetch more backend pages (via loadNextPage, one at a time) until either
  // enough raw items are loaded for `targetIndex`, the backend runs out of
  // pages, or `maxFetches` is hit. Uses itemsCountRef/nextPageUrlRef (not
  // filtered/totalPages) so the loop condition is re-checked fresh each
  // iteration instead of once against a stale memo.
  async function ensureItemsFor(targetIndex, maxFetches){
    let fetches = 0
    while((targetIndex+1) * PAGE_SIZE > itemsCountRef.current && nextPageUrlRef.current && fetches < maxFetches){
      const ok = await loadNextPage()
      if(!ok) break
      fetches++
    }
  }

  // Advance to the next client-side page, transparently fetching more backend
  // pages first if we're at the edge of what's currently loaded.
  async function goToNextPage(){
    await ensureItemsFor(pageIndex+1, 5)
    setPageIndex(p => p+1)
  }

  // Jump to an arbitrary page number, fetching ahead if it's beyond what's
  // currently loaded (higher cap since a manual jump can span further).
  async function goToPage(targetIndex){
    await ensureItemsFor(targetIndex, 20)
    const maxKnownPage = Math.max(0, Math.ceil(itemsCountRef.current / PAGE_SIZE) - 1)
    setPageIndex(Math.max(0, Math.min(targetIndex, maxKnownPage)))
  }

  // helper: ensure array of items is unique by `id` preserving first occurrence order
  function uniqueById(arr){
    if(!Array.isArray(arr)) return []
    const seen = new Set()
    const out = []
    for(const it of arr){
      const id = it && (it.id || it._id || it.pk || it.external_id)
      if(id == null){
        out.push(it)
        continue
      }
      if(seen.has(id)) continue
      seen.add(id)
      out.push(it)
    }
    return out
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Fanart Viewer</h1>
        <div style={{display:'flex', alignItems:'center', gap:8}}>
          {readOnly && <span style={{fontSize:12, color:'#94a3b8', border:'1px solid #334155', borderRadius:4, padding:'2px 8px'}}>view only</span>}
          <HeaderMenu
            open={headerMenuOpen}
            onOpenChange={setHeaderMenuOpen}
            items={[
            { label: <MenuIconLabel iconNode={<SwipeIcon />} text="Preview Timeline" />, onClick: () => { setPreviewOpen(p => !p); setPreviewInitialItemId(null) }, active: previewOpen, tourId: 'menu-preview-timeline' },
            // exe版はブラウザではなくpywebviewの専用ウィンドウなので、F5/Ctrl+Rの
            // ネイティブなショートカットに頼らず明示的な再読み込み手段を用意 —
            // サーバー側で状態が変わった(認証情報を保存した、他のウィンドウで
            // データを更新した等)後に最新の状態を確実に反映させるため。
            { label: <MenuIconLabel iconNode={<ReloadIcon />} text="再読み込み" />, onClick: () => window.location.reload() },
            ...(readOnly ? [] : [
              { divider: true },
              {
                label: <MenuIconLabel iconNode="💡" text="使い方ガイド" />,
                submenu: [
                  { label: 'はじめの使い方を見る', onClick: () => startTour('A') },
                  { label: '応用編を見る(検索・AI・バックアップ)', onClick: () => startTour('B') },
                ],
              },
              { divider: true },
              {
                label: (
                  <MenuIconLabel iconNode={<FetchQueueIcon />} text={
                    bulkFetchRunning
                      ? `取得キュー (取得中 ${bulkFetchProgress ? bulkFetchProgress.done : 0}/${bulkFetchProgress ? bulkFetchProgress.total : '?'})`
                      : '取得キュー'
                  } />
                ),
                onClick: () => setFetchQueueOpen(true),
                badge: fetchQueue.length > 0 ? fetchQueue.length : null,
                tourId: 'menu-fetch-queue',
              },
              { label: <MenuIconLabel iconNode={<EditQueueIcon />} text="編集キュー" />, onClick: () => { setEditQueueMounted(true); setEditQueueOpen(true) }, tourId: 'menu-edit-queue' },
              { label: <MenuIconLabel iconNode={<RegionQueueIcon />} text="領域ラベル付けキュー" />, onClick: () => { setRegionQueueMounted(true); setRegionQueueOpen(true) }, tourId: 'menu-region-queue' },
              { label: <MenuIconLabel iconNode="✋" text="手動でアイテムを追加" />, onClick: () => setManualAddOpen(true), tourId: 'menu-manual-add' },
              { divider: true },
              {
                label: 'キャラクター',
                tourId: 'menu-group-character',
                submenu: [
                  { label: 'キャラクターグループ', onClick: () => setCharGroupOpen(true), tourId: 'menu-character-groups' },
                  { label: 'キャラクター別名グループ', onClick: () => setCharAliasGroupOpen(true), tourId: 'menu-character-alias-groups' },
                  { label: 'キャラ↔Danbooruリンク', onClick: () => setCharLinkOpen(true), tourId: 'menu-character-danbooru-link' },
                ],
              },
              {
                label: <MenuIconLabel icon="/icons/twitter.svg" text="Twitter" />,
                tourId: 'menu-group-twitter',
                submenu: [
                  { label: 'Twitterから画像取得', onClick: () => setTwitterFetchOpen(true), tourId: 'menu-twitter-fetch' },
                  { label: 'Twitter/X 認証情報', onClick: () => setTwitterCredsOpen(true), tourId: 'menu-twitter-creds' },
                ],
              },
              {
                label: <MenuIconLabel icon="/icons/pixiv.svg" text="Pixiv" />,
                tourId: 'menu-group-pixiv',
                submenu: [
                  { label: 'Pixiv 認証情報', onClick: () => setPixivCredsOpen(true), tourId: 'menu-pixiv-creds' },
                  {
                    label: '削除済み作品をpixiv-searchで検索',
                    tourId: 'menu-pixiv-search',
                    onClick: () => {
                      const input = window.prompt('作品IDまたはpixiv.netのURLを入力してください')
                      if (!input || !input.trim()) return
                      const trimmed = input.trim()
                      const m = trimmed.match(/(\d+)/)
                      if (!m) { window.alert('作品IDが見つかりませんでした'); return }
                      window.open(`https://pixiv-search.mgcup.net/search?id=${m[1]}`, '_blank', 'noopener,noreferrer')
                    },
                  },
                ],
              },
              { label: <MenuIconLabel icon="/icons/poipiku.svg" text="Poipiku 認証情報" />, onClick: () => setPoipikuCredsOpen(true), tourId: 'menu-poipiku-creds' },
              { label: <MenuIconLabel iconNode={<BackupIcon />} text="バックアップ" />, onClick: () => setBackupOpen(true), tourId: 'menu-backup' },
              { label: <MenuIconLabel iconNode={<BrainGearIcon />} text="分類器の学習" />, onClick: () => setTrainClassifierOpen(true), tourId: 'menu-train-classifier' },
            ]),
            ...(role !== 'none' ? [
              { divider: true },
              { label: 'ログアウト', onClick: onLogout },
            ] : []),
          ]} />
        </div>
      </header>
      <SearchBar
        query={query}
        setQuery={setQuery}
        suggestions={suggestions}
        onAddSuggestion={addFilter}
        filters={filters}
        onRemoveFilter={removeFilter}
        includeCP={includeCP}
        setIncludeCP={setIncludeCP}
        includeR18={includeR18}
        setIncludeR18={setIncludeR18}
        previewOpen={previewOpen}
        setPreviewOpen={setPreviewOpen}
        situationFilter={situationFilter}
        setSituationFilter={setSituationFilter}
        titleMissingOnly={titleMissingOnly}
        setTitleMissingOnly={setTitleMissingOnly}
        previewMissingOnly={previewMissingOnly}
        setPreviewMissingOnly={setPreviewMissingOnly}
        readOnly={readOnly}
      />
      <ScrollList items={paginatedItems} readOnly={readOnly} onEnqueueFetch={enqueueFetchResult} onOpenPreview={openPreviewForItem} onAddFilter={addFilter} />
      {nextPageUrl && (
        <div className="load-more" style={{margin:'12px 0'}}>
          <button className="btn" onClick={loadNextPage} disabled={loadingPages}>{loadingPages ? 'Loading…' : 'Load more pages'}</button>
          <span style={{marginLeft:12, color:'#666'}}>{backgroundIndexing ? 'Indexing all items in background…' : 'More pages available from server'}</span>
        </div>
      )}
      {filtered.length > PAGE_SIZE && (
        <Pagination
          page={pageIndex}
          totalPages={totalPages}
          onGoToPage={goToPage}
          onPrev={()=>setPageIndex(p=>Math.max(0, p-1))}
          onNext={goToNextPage}
          prevDisabled={pageIndex===0}
          nextDisabled={pageIndex>=totalPages-1 && !nextPageUrl}
          resultsLabel={`${filtered.length} results`}
        />
      )}
      {previewOpen && (
        <React.Suspense fallback={<div className="preview-loading">Loading previews…</div>}>
          <PreviewPane open={previewOpen} onClose={closePreview} readOnly={readOnly} filteredItems={filtered} initialItemId={previewInitialItemId} />
        </React.Suspense>
      )}
      {fetchQueueOpen && (
        <FetchQueueManager
          queue={fetchQueue}
          onRemove={removeFromFetchQueue}
          onClose={()=>setFetchQueueOpen(false)}
          currentPageItems={paginatedItems}
          onEnqueueFetch={enqueueFetchResult}
          bulkRunning={bulkFetchRunning}
          bulkProgress={bulkFetchProgress}
          bulkSummary={bulkFetchSummary}
          onRunBulkFetch={runBulkFetch}
          onCancelBulkFetch={cancelBulkFetch}
        />
      )}
      {editQueueMounted && (
        <EditQueueManager
          hidden={!editQueueOpen}
          onClose={()=>setEditQueueOpen(false)}
          allItems={filtered}
          pageSize={PAGE_SIZE}
          initialPage={pageIndex}
          onPopOut={() => { popOutQueue('editQueue', filtered, PAGE_SIZE, pageIndex); setEditQueueOpen(false) }}
        />
      )}
      {regionQueueMounted && (
        <RegionLabelQueueManager
          hidden={!regionQueueOpen}
          onClose={()=>setRegionQueueOpen(false)}
          allItems={filtered}
          pageSize={PAGE_SIZE}
          initialPage={pageIndex}
          onPopOut={() => { popOutQueue('regionQueue', filtered, PAGE_SIZE, pageIndex); setRegionQueueOpen(false) }}
        />
      )}
      {charGroupOpen && <CharacterGroupManager onClose={()=>setCharGroupOpen(false)} />}
      {charAliasGroupOpen && <CharacterAliasGroupManager onClose={()=>setCharAliasGroupOpen(false)} />}
      {charLinkOpen && <CharacterDanbooruLinkManager onClose={()=>setCharLinkOpen(false)} />}
      {backupOpen && <BackupManager onClose={()=>setBackupOpen(false)} />}
      {trainClassifierOpen && <TrainClassifierManager onClose={()=>setTrainClassifierOpen(false)} />}
      {tourPrompt && (
        <div className="cgm-panel-backdrop" onClick={() => setTourPrompt(null)}>
          <div className="cgm-panel" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
            <div className="cgm-panel-body" style={{ padding: 20 }}>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>
                {tourPrompt === 'welcome' ? 'ようこそ、Fanart Viewerへ' : 'アイテムが増えてきました'}
              </div>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 18, lineHeight: 1.6 }}>
                {tourPrompt === 'welcome'
                  ? '基本的な使い方(認証・取得・リンク切れ対策)を簡単に案内しましょうか？'
                  : '検索・AI提案・バックアップなど、さらに便利な使い方を案内しましょうか？'}
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button className="btn" style={{ background: '#e5e7eb', color: '#111' }} onClick={() => setTourPrompt(null)}>
                  スキップ
                </button>
                <button className="btn" onClick={() => { const g = tourPrompt === 'welcome' ? 'A' : 'B'; setTourPrompt(null); startTour(g) }}>
                  見る
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {tourActive && (
        <Tour
          steps={tourActive === 'A' ? buildTourStepsA() : buildTourStepsB()}
          onClose={closeTour}
          onMenuNeed={setHeaderMenuOpen}
        />
      )}
      {manualAddOpen && <ManualAddItem onClose={()=>setManualAddOpen(false)} onCreated={handleItemCreated} />}
      {pendingNewItem && (
        <EditFields
          item={pendingNewItem}
          onClose={()=>setPendingNewItem(null)}
          onSaved={(updated)=>{
            setItems(prev => Array.isArray(prev) ? prev.map(it => it.id === updated.id ? { ...it, ...updated } : it) : prev)
            setPendingNewItem(null)
          }}
        />
      )}
      {twitterFetchOpen && <TwitterFetchManager onClose={()=>setTwitterFetchOpen(false)} onEnqueueFetch={enqueueFetchResult} />}
      {twitterCredsOpen && <TwitterCredsManager onClose={()=>setTwitterCredsOpen(false)} />}
      {pixivCredsOpen && <PixivCredsManager onClose={()=>setPixivCredsOpen(false)} />}
      {poipikuCredsOpen && <PoipikuCredsManager onClose={()=>setPoipikuCredsOpen(false)} />}
    </div>
  )
}

const ADMIN_PATH = import.meta.env.VITE_ADMIN_PATH || ''

// Thin auth wrapper — handles login state and renders AppMain once authenticated.
export default function App() {
  const [role, setRole] = useState(null) // null=checking, 'login', 'admin', 'viewer', 'none'

  useEffect(() => {
    fetch('/api/auth/')
      .then(r => r.json())
      .then(j => {
        if (!j.auth_required) { setRole('none'); return }
        const saved = localStorage.getItem('fv_role')
        if (saved) {
          fetch('/api/items/?page_size=1').then(r => {
            if (r.ok) setRole(saved)
            else { localStorage.removeItem('fv_token'); localStorage.removeItem('fv_role'); setRole('login') }
          }).catch(() => setRole('login'))
        } else {
          setRole('login')
        }
      })
      .catch(() => setRole('none'))
  }, [])

  function handleLogin(newRole) { setRole(newRole) }

  function handleLogout() {
    localStorage.removeItem('fv_token')
    localStorage.removeItem('fv_role')
    setRole('login')
  }

  if (role === null) return null
  if (role === 'login') {
    const isAdminLogin = Boolean(ADMIN_PATH) && window.location.pathname === `/${ADMIN_PATH}`
    return <LoginScreen onLogin={handleLogin} isAdmin={isAdminLogin} />
  }

  // Same-origin popped-out queue window (see openStandaloneWindow/popOutQueue
  // in AppMain) — cookies/localStorage are shared automatically, so auth
  // above this point already applies unchanged; only the rendered content
  // differs. Reads back whatever page snapshot popOutQueue handed off right
  // before opening this window (and removes it immediately — it's a one-shot
  // handoff, not something later re-reads should see stale data from); if
  // none is present (e.g. this URL was opened directly, with no opener), the
  // queue managers themselves fall back to querying the server unscoped.
  const standalonePanel = new URLSearchParams(window.location.search).get('panel')
  if (standalonePanel === 'editQueue' || standalonePanel === 'regionQueue') {
    let handoff = null
    try {
      const raw = localStorage.getItem(`fv-queue-handoff-${standalonePanel}`)
      if (raw) {
        handoff = JSON.parse(raw)
        localStorage.removeItem(`fv-queue-handoff-${standalonePanel}`)
      }
    } catch (e) {
      console.error('Failed to read queue page handoff', e)
    }
    const commonProps = {
      standalone: true,
      onClose: () => window.close(),
      allItems: handoff ? handoff.allItems : null,
      pageSize: (handoff && handoff.pageSize) || 50,
      initialPage: (handoff && handoff.initialPage) || 0,
    }
    if (standalonePanel === 'editQueue') return <EditQueueManager {...commonProps} />
    return <RegionLabelQueueManager {...commonProps} />
  }

  return <AppMain role={role} onLogout={handleLogout} />
}
