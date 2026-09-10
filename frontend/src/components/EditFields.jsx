import React, { useState, useEffect } from 'react'
import CharacterPicker from './CharacterPicker'
import { getPlatformIcon } from '../lib/platformIcon'

function getCookie(name){
  const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return match ? match.pop() : ''
}

const SECTION = {
  label: { color:'#94a3b8', fontSize:11, fontWeight:600, letterSpacing:'0.08em', textTransform:'uppercase', marginBottom:6, display:'block' },
  wrap:  { marginBottom:16, background:'#0f172a', borderRadius:8, padding:'12px 14px' },
}

const chipStyle = {
  background:'#1e3a5f', color:'#93c5fd', borderRadius:4,
  padding:'5px 10px', fontSize:13, display:'inline-flex', alignItems:'center', gap:6,
}

// Renders text with #hashtags visually distinguished, so a quick glance
// confirms whether _extract_full_text actually captured the hashtags a
// post used (the signal _match_hashtags relies on) rather than requiring
// the user to compare against the source tweet by eye. The split pattern
// mirrors the backend's own extraction regex exactly (item/views.py's
// `_HASHTAG_RE = re.compile(r'#(\w+)', re.UNICODE)`) — \w in Python is
// Unicode-aware (matches Japanese letters too), so this uses \p{L}\p{N}_
// with the `u` flag as JS's equivalent. Previously this used a much more
// permissive `#[^\s#]+` pattern (stops only at whitespace/#), so e.g.
// "#鬼滅の刃/劇場版" was highlighted in full even though the backend only
// ever matches up to the slash ("鬼滅の刃") — misleadingly implying more
// was recognized than actually gets matched by _match_hashtags.
function HighlightedText({ text }){
  const parts = String(text || '').split(/(#[\p{L}\p{N}_]+)/gu)
  return parts.map((part, i) => (
    part.startsWith('#')
      ? <span key={i} style={{color:'#93c5fd', fontWeight:600}}>{part}</span>
      : <React.Fragment key={i}>{part}</React.Fragment>
  ))
}

function TagField({ label, hint, list, setList, allOptions, setAllOptions, selectPlaceholder }){
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)

  const available = allOptions.filter(t=>!list.includes(t))
  const q = query.trim().toLowerCase()
  const filtered = q ? available.filter(t=>t.toLowerCase().includes(q)) : available
  const exactExists = q && [...available, ...list].some(t=>t.toLowerCase()===q)

  function addExisting(t){
    if(!list.includes(t)) setList(prev=>[...prev, t])
    setQuery('')
  }

  function addNew(){
    const t = query.trim()
    if(!t) return
    if(!list.includes(t)) setList(prev=>[...prev, t])
    if(!allOptions.includes(t)) setAllOptions(prev=>[...prev, t].sort())
    setQuery('')
  }

  return (
    <div style={SECTION.wrap}>
      <label style={SECTION.label}>{label}</label>
      {hint && <div style={{fontSize:12, color:'#64748b', marginBottom:8}}>{hint}</div>}
      <div style={{display:'flex', flexWrap:'wrap', gap:6, marginBottom:8, minHeight:28}}>
        {list.length === 0
          ? <span style={{fontSize:13, color:'#475569'}}>未選択</span>
          : list.map(t=>(
            <span key={t} style={chipStyle}>
              {t}
              <button onClick={()=>setList(prev=>prev.filter(x=>x!==t))}
                style={{border:'none', background:'none', cursor:'pointer', padding:0, lineHeight:1, fontSize:16, color:'#93c5fd'}}>×</button>
            </span>
          ))}
      </div>
      <div style={{position:'relative'}}>
        <input
          style={{width:'100%', background:'#1e293b', color:'#f1f5f9', border:'1px solid #334155',
            borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
          placeholder={selectPlaceholder}
          value={query}
          onChange={e=>setQuery(e.target.value)}
          onFocus={()=>setFocused(true)}
          onBlur={()=>setTimeout(()=>setFocused(false), 150)}
          onKeyDown={e=>{
            if(e.key!=='Enter') return
            if(filtered.length===1) addExisting(filtered[0])
            else if(q && !exactExists) addNew()
          }}
        />
        {focused && (
          <div style={{position:'absolute', top:'100%', left:0, right:0, marginTop:4, background:'#1e293b',
            border:'1px solid #334155', borderRadius:6, maxHeight:180, overflowY:'auto', zIndex:10,
            boxShadow:'0 8px 24px rgba(0,0,0,0.4)'}}>
            {filtered.slice(0, 30).map(t=>(
              <button key={t} onMouseDown={e=>e.preventDefault()} onClick={()=>addExisting(t)}
                style={{display:'block', width:'100%', textAlign:'left', background:'none', border:'none',
                  color:'#f1f5f9', padding:'8px 12px', fontSize:14, cursor:'pointer'}}
                onMouseEnter={e=>e.currentTarget.style.background='#334155'}
                onMouseLeave={e=>e.currentTarget.style.background='none'}
              >{t}</button>
            ))}
            {q && !exactExists && (
              <button onMouseDown={e=>e.preventDefault()} onClick={addNew}
                style={{display:'block', width:'100%', textAlign:'left', background:'none', border:'none',
                  borderTop: filtered.length>0 ? '1px solid #334155' : 'none',
                  color:'#93c5fd', padding:'8px 12px', fontSize:14, cursor:'pointer'}}
              >＋ 「{query.trim()}」を新規作成</button>
            )}
            {filtered.length===0 && !q && (
              <div style={{padding:'8px 12px', fontSize:13, color:'#64748b'}}>候補なし</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// The actual edit form, with no modal chrome of its own — reused both by
// EditFields (wraps it in a fixed-position modal, for the per-row ✎ button)
// and EditQueueManager (embeds it directly in a mailbox-style bulk review
// panel, where "cancel" means skip-to-next rather than close-a-modal).
export function ItemEditForm({ item, onClose, onSaved, closeLabel = 'キャンセル', initialSuggestion = null }){
  const [titleList, setTitleList] = useState(item.titles||[])
  const [charList,  setCharList]  = useState(item.characters||[])
  const [situation, setSituation] = useState((item.situation||'').toUpperCase())
  const [tags,      setTags]      = useState((item.tags||[]).join(', '))
  const [artist,    setArtist]    = useState(item.artist||'')
  const [loading,   setLoading]   = useState(false)
  const [allTitles, setAllTitles] = useState([])
  const [allChars,  setAllChars]  = useState([])
  const [suggesting, setSuggesting] = useState(false)
  const [suggestError, setSuggestError] = useState('')
  // null = not tried yet. Otherwise {added, source, sampleSize} — `added`
  // specifically tracks whether anything was actually filled in, since a
  // "checked but found nothing to suggest" result (common for an artist
  // with no other tagged items yet) is a real, valid outcome and must not
  // be shown the same way as "suggestion applied" (see applySuggestion).
  const [suggestionResult, setSuggestionResult] = useState(null)
  // Low-confidence title options — offered as one-click picks, never
  // auto-applied like the rest of applySuggestion (see suggest_tags_view's
  // title_candidates: returned only when nothing was confident enough to
  // suggest outright, so guessing wrong here is a real risk worth avoiding).
  const [titleCandidates, setTitleCandidates] = useState([])
  // Off by default — the one network call in the suggestion pipeline
  // (Danbooru reverse lookup for a tagger-recognized character that
  // matches nothing in this app's own vocabulary yet) is opt-in.
  const [suggestExternal, setSuggestExternal] = useState(false)
  // On by default — the weighted-ensemble combiner
  // (_suggest_for_item_ensemble) is now suggest_tags_view's own default
  // too. A real 10-seed evaluation showed it winning on character accuracy
  // in every sampled seed (avg 62.6% vs the old cascade's 51.0%);
  // unchecking this opts back into the original "first source wins"
  // cascade instead.
  const [suggestUseEnsemble, setSuggestUseEnsemble] = useState(true)
  // Up to 3 ranked character candidates from the last suggestion response,
  // awaiting review — NOT auto-applied to charList (see applySuggestion's
  // own comment on why: an ensemble/cascade "match" can still be the wrong
  // person, and silently committing every candidate it returns was an
  // actual bug this replaced — see _suggest_for_item's own comment on
  // filtering unmatched tagger candidates). Each candidate carries
  // `contributors` (see _character_breakdown) so a human can tell whether
  // a wrong-looking suggestion traces back to the tagger's own
  // recognition, the Danbooru link table, or neither ever firing at all.
  const [charCandidates, setCharCandidates] = useState([])
  const [expandedCandidate, setExpandedCandidate] = useState(null)
  // Only offered once tagger_capabilities/ confirms the heavier 'timm'
  // backend is actually installed on this server (see requirements-timm.txt
  // — not every deployment opts into torch).
  const [suggestModel, setSuggestModel] = useState('default')
  const [haveTimm, setHaveTimm] = useState(false)
  // This item's individual preview images (see ItemViewSet.previews), so
  // the user can pick a specific one to run inference on instead of always
  // the single largest image (suggest_tags_view's own default — see
  // _select_image_bytes). null = "let the server pick" (largest image).
  const [images, setImages] = useState([])
  const [selectedImageIndex, setSelectedImageIndex] = useState(null)

  useEffect(()=>{
    fetch('/api/items/all_titles/')
      .then(r=>r.json()).then(d=>{ if(Array.isArray(d)) setAllTitles(d) }).catch(()=>{})
    fetch('/api/items/all_characters/')
      .then(r=>r.json()).then(d=>{ if(Array.isArray(d)) setAllChars(d) }).catch(()=>{})
    fetch('/api/items/tagger_capabilities/')
      .then(r=>r.json()).then(d=>setHaveTimm(!!d.have_timm)).catch(()=>{})
    fetch(`/api/items/${item.id}/previews/`)
      .then(r=>r.json()).then(d=>{ if(Array.isArray(d)) setImages(d) }).catch(()=>{})
  }, [item.id])

  function parseList(str){
    if(str == null) return []
    return String(str).split(',').map(s=>s.trim()).filter(s=>s.length>0)
  }

  // Merges a tagger result (characters/tags/situation_hint) into the form as
  // plain suggestions — nothing here is saved until the user hits 保存, and
  // every field stays fully editable/removable afterwards (see TagField /
  // CharacterPicker chip UI). Shared by the manual "AIで提案" button and by
  // a bulk-suggested result handed down via `initialSuggestion` (see the
  // effect below) so both paths behave identically.
  function applySuggestion(j){
    let added = false

    // Titles inferred by cross-referencing matched characters' groups (the
    // tagger itself can't suggest titles — its public tag list has no
    // copyright/series tags at all).
    setTitleList(prev => {
      const toAdd = (j.suggested_titles || []).filter(t => !prev.includes(t))
      if(toAdd.length === 0) return prev
      added = true
      return [...prev, ...toAdd]
    })
    // Characters are NOT auto-applied — up to 3 ranked candidates are
    // surfaced for review instead (see charCandidates' own comment). A
    // "matched" candidate can still be the wrong person (a tagger
    // misidentification, or a Danbooru link pointing at the wrong tag),
    // so silently committing every candidate returned was an actual bug
    // this replaced, not just an interaction-design choice.
    const newCandidates = j.characters || []
    if(newCandidates.length > 0) added = true
    setCharCandidates(newCandidates)
    setExpandedCandidate(null)
    setTags(prev => {
      const existing = parseList(prev)
      const toAdd = (j.tags || []).map(t => t.name).filter(n => !existing.includes(n))
      if(toAdd.length === 0) return prev
      added = true
      return [...existing, ...toAdd].join(', ')
    })
    setSituation(prev => {
      if(!j.situation_hint || prev) return prev
      added = true
      return j.situation_hint
    })

    setTitleCandidates(j.title_candidates || [])

    // j.image_index: which PreviewImage the tagger actually ran on (null
    // when there was no image to infer from, e.g. tags-only result from
    // hashtags/DB history). Surfaced in the UI so it's never ambiguous
    // which image a suggestion is based on — see _select_image_bytes.
    setSuggestionResult({ added, source: j.source || null, sampleSize: j.sample_size ?? null, imageIndex: j.image_index ?? null })
  }

  function acceptTitleCandidate(t){
    setTitleList(prev => prev.includes(t) ? prev : [...prev, t])
    setTitleCandidates(prev => prev.filter(x => x !== t))
  }

  // Toggle (not one-way accept): a candidate stays visible with its
  // diagnostic breakdown after being added, in case its contributors are
  // still worth checking, or the user changes their mind — unlike
  // acceptTitleCandidate, which removes the option once taken (titles have
  // no per-candidate diagnostics to keep referring back to).
  function toggleCharCandidate(name){
    setCharList(prev => prev.includes(name) ? prev.filter(c => c !== name) : [...prev, name])
  }

  const SOURCE_LABELS = {
    hashtag: 'ハッシュタグ', artist_history: '作家履歴(強)', artist_history_weak: '作家履歴(弱)',
    tag_similarity: 'タグ類似度(強)', tag_similarity_weak: 'タグ類似度(弱)',
    tagger: 'タガー直接認識', tagger_group: 'タガー+キャラグループ', classifier: '独自分類器', danbooru: 'Danbooru照合',
  }
  const MATCH_METHOD_LABELS = { direct: '既存表記と直接一致', danbooru_link: 'Danbooruリンク経由で翻訳' }

  // "この画像/投稿を直接見て判断した"わけではなく、"他のアイテムから類推した"
  // ソース — 単独だと誤りやすいので、候補カード上に常時マーカーを出す(展開しな
  // いと見えない根拠パネルとは別に)。hashtag/tagger/classifier/danbooru は
  // このアイテム自身の内容(投稿文・画像)を直接見ているので対象外。
  const LOW_PRIORITY_SOURCES = new Set(['artist_history', 'artist_history_weak', 'tag_similarity', 'tag_similarity_weak'])

  function candidateSourceTier(contributors){
    if(!contributors || contributors.length === 0) return null
    const sources = new Set(contributors.map(ct => ct.source))
    const hasLow = [...sources].some(s => LOW_PRIORITY_SOURCES.has(s))
    if(!hasLow) return null
    const hasNormal = [...sources].some(s => !LOW_PRIORITY_SOURCES.has(s))
    return hasNormal ? 'mixed' : 'low_only'
  }

  // If EditQueueManager already ran bulk suggestion for this item, apply the
  // cached result immediately instead of re-running inference (~5s+/image).
  useEffect(()=>{
    if(initialSuggestion) applySuggestion(initialSuggestion)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function runSuggest(){
    if(suggestModel === 'canary' && !window.confirm('最新モデルは初回選択時にサーバー側で大きいモデル(約1.3GB)をダウンロードします。時間がかかる場合があります。続行しますか？')) return
    setSuggesting(true)
    setSuggestError('')
    try{
      const resp = await fetch(`/api/items/${item.id}/suggest_tags/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          external: suggestExternal,
          model: suggestModel === 'canary' ? 'timm' : 'default',
          use_ensemble: suggestUseEnsemble,
          image_index: selectedImageIndex,
        }),
      })
      const j = await resp.json().catch(()=>({}))
      if(!resp.ok){ setSuggestError(j.detail || `提案の取得に失敗しました (${resp.status})`); return }
      applySuggestion(j)
    }catch(e){
      setSuggestError('提案の取得に失敗しました: ' + (e && e.message ? e.message : String(e)))
    }finally{
      setSuggesting(false)
    }
  }

  async function save(){
    const payload = {
      titles: titleList,
      characters: charList,
      situation,
      tags: tags.trim()===''? [] : parseList(tags),
      artist: artist.trim(),
    }
    setLoading(true)
    try{
      const resp = await fetch(`/api/items/${item.id}/update_fields/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      })
      const j = await resp.json().catch(()=>({}))
      setLoading(false)
      if(!resp.ok){ alert('Save failed: ' + (j.detail || JSON.stringify(j))); return }
      // Backend auto-creates a CharacterGroup the moment a save first
      // introduces both a brand-new title and a brand-new character
      // together (see views._maybe_autocreate_character_group) — surfaced
      // here so it isn't a silent side effect the user only discovers
      // later in the character-group manager.
      if(j.auto_created_character_group){
        alert(`新しいタイトル「${j.auto_created_character_group.name}」のキャラクターグループを自動作成しました。`)
      }
      if(onSaved) onSaved(j.item)
    }catch(e){
      setLoading(false)
      alert('Save failed: ' + e.message)
    }
  }

  const SITUATIONS = [
    { value:'',        label:'— 未設定 —' },
    { value:'SOLO',    label:'SOLO' },
    { value:'CP',      label:'CP' },
    { value:'MULTIPLE',label:'MULTIPLE' },
    { value:'PARODY',  label:'PARODY' },
    { value:'R18',     label:'R18' },
  ]

  return (
    <div>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:20}}>
        <span style={{color:'#f8fafc', fontWeight:700, fontSize:16}}>Edit #{item.id}</span>
        <button className="btn" style={{padding:'4px 10px'}} onClick={onClose}>✕</button>
      </div>

      {/* キャラを記入する前に実際の画像を確認できるように — 元リンクを
          開く(新しいタブ、このフォームは閉じない)か、この画像自体を新しい
          タブで開く。編集キューを閉じずに済むので、いちいち別ウィンドウを
          開き直す必要がない(このフォームの入力内容もそのまま残る)。 */}
      <div style={{...SECTION.wrap, background:'#0f172a', display:'flex', alignItems:'center', gap:16, flexWrap:'wrap'}}>
        <img
          src={`/api/items/${item.id}/preview/${selectedImageIndex !== null ? `?index=${selectedImageIndex}` : ''}`}
          alt=""
          style={{width:120, height:120, objectFit:'cover', borderRadius:6, flexShrink:0, background:'#1e293b'}}
        />
        <div style={{display:'flex', flexDirection:'column', gap:8}}>
          {item.link && (
            <a className="link-text" href={item.link} target="_blank" rel="noreferrer" style={{display:'inline-flex', alignItems:'center', gap:6}}>
              {(() => {
                const platform = getPlatformIcon(item.link)
                return platform ? <img src={platform.icon} alt={platform.label} style={{width:16, height:16, borderRadius:3}} /> : null
              })()}
              元リンクを開く(実際の画像を確認)
            </a>
          )}
          <a className="link-text" href={`/api/items/${item.id}/preview/${selectedImageIndex !== null ? `?index=${selectedImageIndex}` : ''}`} target="_blank" rel="noreferrer" style={{display:'inline-flex', alignItems:'center', gap:6}}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{display:'block'}}><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            この画像を新しいタブで開く
          </a>
        </div>
      </div>

      <div style={{...SECTION.wrap, background:'#0f172a'}}>
        <label style={SECTION.label}>取得した本文(確認用) <span style={{fontWeight:400, textTransform:'none', fontSize:11}}>— ハッシュタグが正しく取れているか確認できます</span></label>
        {item.description ? (
          <div style={{fontSize:13, color:'#cbd5e1', whiteSpace:'pre-wrap', wordBreak:'break-word'}}>
            <HighlightedText text={item.description} />
          </div>
        ) : (
          // Explicit rather than hiding the whole block, so an empty result
          // reads as "nothing was captured for this item" (e.g. imported
          // before description capture existed, or the fetcher used
          // doesn't return post text) instead of looking like the panel
          // itself failed to load.
          <div style={{fontSize:13, color:'#64748b', fontStyle:'italic'}}>
            本文情報なし（取得時に保存されていないか、未取得です）
          </div>
        )}
      </div>

      {images.length > 1 && (
        <div style={{...SECTION.wrap, background:'#0f172a'}}>
          <label style={SECTION.label}>推論に使う画像 <span style={{fontWeight:400, textTransform:'none', fontSize:11}}>— 未選択なら最も大きい画像が自動で使われます</span></label>
          <div style={{display:'flex', flexWrap:'wrap', gap:8}}>
            <button
              onClick={()=>setSelectedImageIndex(null)}
              title="自動選択(最大サイズの画像)"
              style={{
                width:56, height:56, borderRadius:6, cursor:'pointer',
                display:'flex', alignItems:'center', justifyContent:'center',
                background:'#1e293b', color:'#94a3b8', fontSize:11,
                border: selectedImageIndex === null ? '2px solid #3b82f6' : '1px solid #334155',
              }}
            >自動</button>
            {images.map(img => (
              <button
                key={img.index}
                onClick={()=>setSelectedImageIndex(img.index)}
                title={`${img.index + 1}枚目でこの画像に対して提案する`}
                style={{
                  width:56, height:56, borderRadius:6, padding:0, cursor:'pointer', overflow:'hidden', position:'relative',
                  border: selectedImageIndex === img.index ? '2px solid #3b82f6' : '1px solid #334155',
                }}
              >
                <img src={`/api/items/${item.id}/preview/?index=${img.index}`} alt={`${img.index + 1}枚目`}
                  style={{width:'100%', height:'100%', objectFit:'cover', display:'block'}} />
                <span style={{position:'absolute', right:2, bottom:2, fontSize:10, color:'#fff',
                  background:'rgba(0,0,0,0.6)', borderRadius:3, padding:'0 3px'}}>{img.index + 1}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div style={{marginBottom:16}}>
        <button className="btn" onClick={runSuggest} disabled={suggesting} style={{fontSize:13}}>
          {suggesting ? '画像を解析中…' : (suggestionResult ? '🏷 再提案' : '🏷 AIでキャラ・タグを提案')}
        </button>
        <label style={{marginLeft:10, fontSize:11, color:'#94a3b8', cursor:'pointer'}} title="新規タイトルの逆引きに加え、ハッシュタグと登録済みキャラ名の表記違い(例: カタカナ表記のハッシュタグ↔登録済みのローマ字名)もDanbooruの別名情報で突き合わせます">
          <input type="checkbox" checked={suggestExternal} onChange={e=>setSuggestExternal(e.target.checked)} disabled={suggesting} style={{marginRight:4, verticalAlign:'middle'}} />
          Danbooruで新規タイトル・ハッシュタグの表記違いも照合(外部通信)
        </label>
        <label style={{marginLeft:10, fontSize:11, color:'#94a3b8', cursor:'pointer'}} title="複数の情報源を重み付けして統合する新方式(実験的)。従来方式より実データでキャラ推定精度が高いことを確認済み">
          <input type="checkbox" checked={suggestUseEnsemble} onChange={e=>setSuggestUseEnsemble(e.target.checked)} disabled={suggesting} style={{marginRight:4, verticalAlign:'middle'}} />
          統合型の推論を使う(実験的)
        </label>
        {haveTimm && (
          <select value={suggestModel} onChange={e=>setSuggestModel(e.target.value)} disabled={suggesting} style={{marginLeft:10, fontSize:11}}>
            <option value="default">標準モデル(軽量・高速)</option>
            <option value="canary">2026年学習の最新モデル(重い・初回は大きいダウンロード)</option>
          </select>
        )}
        <span style={{marginLeft:8, fontSize:11, color: suggestionResult && !suggestionResult.added ? '#f59e0b' : '#64748b'}}>
          {!suggestionResult ? (
            'プレビュー画像とキャラ既存データからキャラ・タイトル・タグを提案します（保存されるまで確定しません）'
          ) : suggestionResult.added ? (
            <>
              提案を反映済み（保存されるまで確定しません。内容は自由に編集できます）
              {suggestionResult.source && (
                <> — {suggestionResult.source === 'db' ? '既存データから'
                    : suggestionResult.source === 'tagger' ? '画像解析から'
                    : '既存データ＋画像解析から'}
                  {suggestionResult.source.includes('danbooru') && '（Danbooru照合で新規タイトルを推論）'}</>
              )}
              {images.length > 1 && suggestionResult.imageIndex != null && (
                <> （{suggestionResult.imageIndex + 1}枚目の画像を使用）</>
              )}
            </>
          ) : (
            <>
              提案できる情報が見つかりませんでした
              {suggestionResult.sampleSize === 0 && '（この作者の他のアイテムがまだありません）'}
              {suggestionResult.sampleSize > 0 && '（この作者の他のアイテムから十分な傾向が見つかりませんでした）'}
            </>
          )}
        </span>
        {suggestError && <div style={{marginTop:6, fontSize:12, color:'#f87171'}}>{suggestError}</div>}
      </div>

      <TagField
        label="Titles ★"
        hint="このイラストの作品名・シリーズ名を選択してください"
        list={titleList} setList={setTitleList}
        allOptions={allTitles} setAllOptions={setAllTitles}
        selectPlaceholder="タイトルを検索、または新規入力（必須）"
      />

      {titleCandidates.length > 0 && (
        <div style={{marginTop:-10, marginBottom:16, padding:'0 14px'}}>
          <div style={{fontSize:11, color:'#f59e0b', marginBottom:6}}>
            確信度は低いですが、候補です(クリックで追加):
          </div>
          <div style={{display:'flex', flexWrap:'wrap', gap:6}}>
            {titleCandidates.map(t => (
              <button key={t} className="btn" style={{fontSize:12, background:'#78350f', color:'#fef3c7'}} onClick={()=>acceptTitleCandidate(t)}>
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {charCandidates.length > 0 && (
        <div style={{marginBottom:10, padding:'0 14px'}}>
          <div style={{fontSize:11, color:'#94a3b8', marginBottom:6}}>
            AI提案候補(スコア高い順、クリックで追加/解除。どれも違う場合は下のCharactersで直接手動選択してください):
          </div>
          <div style={{display:'flex', flexDirection:'column', gap:6}}>
            {charCandidates.map(c => {
              const isAdded = charList.includes(c.name)
              const isExpanded = expandedCandidate === c.name
              const tier = candidateSourceTier(c.contributors)
              return (
                <div key={c.name} style={{border:'1px solid #334155', borderRadius:6, background:'#1e293b'}}>
                  <div style={{display:'flex', alignItems:'center', gap:8, padding:'6px 10px'}}>
                    <button className="btn" onClick={()=>toggleCharCandidate(c.name)}
                      style={{fontSize:12, background: isAdded ? '#166534' : '#334155', color:'#f1f5f9'}}>
                      {isAdded ? '✓ 追加済み' : '＋ 追加'}
                    </button>
                    <span style={{fontSize:13, color:'#f1f5f9', fontWeight:600}}>{c.name}</span>
                    <span style={{fontSize:11, color:'#94a3b8'}}>score {c.score}</span>
                    {tier === 'low_only' && (
                      <span title="作家履歴・タグ類似度など、他アイテムからの類推のみが根拠です。画像やハッシュタグを直接見て判断したものではありません"
                        style={{fontSize:11, padding:'2px 6px', borderRadius:4, background:'#78350f', color:'#fde68a'}}>
                        ⚠ 類推のみ
                      </span>
                    )}
                    {tier === 'mixed' && (
                      <span title="作家履歴・タグ類似度による類推と、それ以外の根拠(ハッシュタグ/タガー直接認識/分類器など)が両方とも支持しています"
                        style={{fontSize:11, padding:'2px 6px', borderRadius:4, background:'#1e3a8a', color:'#bfdbfe'}}>
                        🔀 混合根拠
                      </span>
                    )}
                    {c.contributors && (
                      <button className="btn" onClick={()=>setExpandedCandidate(isExpanded ? null : c.name)}
                        style={{fontSize:11, marginLeft:'auto', background:'transparent', color:'#60a5fa'}}>
                        {isExpanded ? '詳細を閉じる ▲' : '根拠を見る ▼'}
                      </button>
                    )}
                  </div>
                  {isExpanded && c.contributors && (
                    <div style={{padding:'0 10px 8px', fontSize:11, color:'#cbd5e1'}}>
                      {c.contributors.map((ct, i) => (
                        <div key={i} style={{padding:'3px 0', borderTop: i>0 ? '1px solid #334155' : 'none'}}>
                          <span style={{color:'#93c5fd'}}>{SOURCE_LABELS[ct.source] || ct.source}</span>
                          {' '}(確信度 {ct.confidence})
                          {ct.raw_name && ct.raw_name !== c.name && (
                            <> — タガー認識: <span style={{color:'#fbbf24'}}>「{ct.raw_name}」</span>
                              {ct.match_method && <> → {MATCH_METHOD_LABELS[ct.match_method] || ct.match_method}</>}
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div style={SECTION.wrap}>
        <label style={SECTION.label}>Characters</label>
        <CharacterPicker charList={charList} setCharList={setCharList} allChars={allChars} titles={titleList} />
      </div>

      <div style={SECTION.wrap}>
        <label style={SECTION.label}>Artist</label>
        <input
          style={{width:'100%', background:'#0f172a', color:'#f1f5f9', border:'1px solid #334155',
            borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
          value={artist} onChange={e=>setArtist(e.target.value)}
          placeholder="作者名 / Twitter ID"
        />
      </div>

      <div style={SECTION.wrap}>
        <label style={SECTION.label}>Situation</label>
        <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
          {SITUATIONS.map(s=>(
            <button key={s.value} onClick={()=>setSituation(s.value)}
              style={{
                padding:'8px 16px', borderRadius:6, border:'none', cursor:'pointer', fontSize:13, fontWeight:500,
                background: situation===s.value ? '#3b82f6' : '#0f172a',
                color: situation===s.value ? '#fff' : '#94a3b8',
                outline: situation===s.value ? '2px solid #3b82f6' : '1px solid #334155',
              }}
            >{s.label || '—'}</button>
          ))}
        </div>
      </div>

      <div style={SECTION.wrap}>
        <label style={SECTION.label}>Tags <span style={{fontWeight:400, textTransform:'none', fontSize:11}}>(カンマ区切り)</span></label>
        <input
          style={{width:'100%', background:'#0f172a', color:'#f1f5f9', border:'1px solid #334155',
            borderRadius:6, padding:'9px 12px', fontSize:14, boxSizing:'border-box'}}
          value={tags} onChange={e=>setTags(e.target.value)}
          placeholder="tag1, tag2, tag3"
        />
      </div>

      <div style={{display:'flex', gap:8, marginTop:4}}>
        <button className="btn" style={{background:'#3b82f6', color:'#fff', padding:'10px 24px', fontSize:14, fontWeight:600}}
          onClick={save} disabled={loading}>
          {loading ? '保存中…' : '保存'}
        </button>
        <button className="btn" style={{padding:'10px 16px'}} onClick={onClose}>{closeLabel}</button>
      </div>
    </div>
  )
}

export default function EditFields({ item, onClose, onSaved }){
  return (
    <div style={{position:'fixed', left:0, right:0, top:0, bottom:0, background:'rgba(0,0,0,0.65)', zIndex:1300}} onClick={onClose}>
      <div
        style={{width:540, maxWidth:'92%', margin:'3% auto', background:'#1e293b',
          borderRadius:12, padding:'20px 24px', maxHeight:'92vh', overflowY:'auto',
          boxShadow:'0 12px 48px rgba(0,0,0,0.7)'}}
        onClick={e=>e.stopPropagation()}
      >
        <ItemEditForm item={item} onClose={onClose} onSaved={(newItem)=>{ if(onSaved) onSaved(newItem); onClose() }} />
      </div>
    </div>
  )
}
