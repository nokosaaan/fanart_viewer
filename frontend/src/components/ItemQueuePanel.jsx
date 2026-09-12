import React, { useState, useRef, useEffect } from 'react'
import { ItemEditForm } from './EditFields'
import RegionAnnotator from './RegionAnnotator'
import { saveItemFields, saveCharacterRegions } from '../lib/itemFieldsApi'

// One combined "編集 + 領域ラベル付け" screen for a single item — replaces
// having to open the same image twice, once in the edit queue and once in
// the region-label queue (see ItemQueueManager.jsx's own top-level comment
// for the full background). Embeds ItemEditForm and RegionAnnotator with
// their own save/close button rows suppressed (`showOwnActions=false`) and
// owns one combined "保存" button instead.
//
// Region section visibility tracks the situation field LIVE (before it's
// even saved) via ItemEditForm's onSituationChange — a SOLO/R18 item has
// nothing to disambiguate, so switching an item's situation into/out of one
// of those two values shows/hides the section immediately, without forcing
// a save first just to see it.
export default function ItemQueuePanel({ item, onClose, onSaved, onDirtyChange, closeLabel = 'スキップ（後で対応）', initialSuggestion = null }){
  const [situationDraft, setSituationDraft] = useState((item.situation || '').toUpperCase())
  const [titlesDraft, setTitlesDraft] = useState(item.titles || [])
  const [fieldsDirty, setFieldsDirty] = useState(false)
  const [regionDirty, setRegionDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Plain mutable refs (not forwardRef/useImperativeHandle — this codebase
  // has no existing usage of that pattern anywhere; see ItemEditForm's
  // fieldsRef / RegionAnnotator's boxesRef for the producing side).
  const fieldsRef = useRef(null)
  const boxesRef = useRef(null)

  const showRegionSection = situationDraft !== 'SOLO' && situationDraft !== 'R18'

  // RegionAnnotator unmounts the instant the section hides (switching
  // situation to SOLO/R18) — its boxesRef/onDirtyChange calls stop then and
  // there, so anything it had recorded must be cleared here too, or a stale
  // boxesRef.current (from before the unmount) would get saved anyway on
  // the next handleSave despite the section no longer being shown.
  useEffect(()=>{
    if(!showRegionSection){
      setRegionDirty(false)
      boxesRef.current = null
    }
  }, [showRegionSection])

  useEffect(()=>{
    if(onDirtyChange) onDirtyChange(fieldsDirty || regionDirty)
  }, [fieldsDirty, regionDirty, onDirtyChange])

  async function handleSave(){
    if(!fieldsRef.current) return
    setSaving(true)
    setError('')
    try{
      // Order is mandatory: update_fields OVERWRITES item.characters
      // wholesale, while character_regions_view only ADDS to it — saving
      // fields first, then regions, is the only ordering that can't lose a
      // region-derived character name to the fields overwrite (see
      // views.py's update_fields/character_regions_view — confirmed safe
      // during planning, no backend change needed).
      const payload = fieldsRef.current.getPayload()
      const j = await saveItemFields(item.id, payload)
      let latestItem = j.item

      // Same two auto-linking notices ItemEditForm.save() itself would show
      // — replicated here since this path calls saveItemFields directly
      // rather than going through ItemEditForm's own save().
      if(j.auto_created_character_group){
        alert(`新しいタイトル「${j.auto_created_character_group.name}」のキャラクターグループを自動作成しました。`)
      }
      if(j.auto_assigned_to_character_group){
        alert(`新しいキャラクターをキャラクターグループ「${j.auto_assigned_to_character_group.name}」に自動的に割り当てました。`)
      }

      if(regionDirty && boxesRef.current){
        const regions = boxesRef.current.getPayload()
        const j2 = await saveCharacterRegions(item.id, regions)
        latestItem = j2.item
      }

      setFieldsDirty(false)
      setRegionDirty(false)
      if(onSaved) onSaved(latestItem)
    }catch(e){
      setError('保存に失敗しました: ' + (e && e.message ? e.message : String(e)))
    }finally{
      setSaving(false)
    }
  }

  return (
    <div>
      <ItemEditForm
        item={item}
        onClose={onClose}
        closeLabel={closeLabel}
        initialSuggestion={initialSuggestion}
        showOwnActions={false}
        fieldsRef={fieldsRef}
        onSituationChange={setSituationDraft}
        onTitlesChange={setTitlesDraft}
        onDirtyChange={setFieldsDirty}
      />

      {showRegionSection && (
        <div style={{marginTop:16, background:'#0f172a', borderRadius:8, padding:'12px 14px'}}>
          <label style={{color:'#94a3b8', fontSize:11, fontWeight:600, letterSpacing:'0.08em', textTransform:'uppercase', marginBottom:10, display:'block'}}>
            領域ラベル付け <span style={{fontWeight:400, textTransform:'none', fontSize:11}}>— 複数キャラが写っている場合、どの矩形が誰かを割り当ててください</span>
          </label>
          <RegionAnnotator
            item={item}
            titles={titlesDraft}
            showOwnActions={false}
            boxesRef={boxesRef}
            onDirtyChange={setRegionDirty}
          />
        </div>
      )}

      {error && <div style={{marginTop:10, fontSize:12, color:'#f87171'}}>{error}</div>}

      <div style={{display:'flex', gap:8, marginTop:16}}>
        <button className="btn" style={{background:'#3b82f6', color:'#fff', padding:'10px 24px', fontSize:14, fontWeight:600}}
          onClick={handleSave} disabled={saving}>
          {saving ? '保存中…' : '保存'}
        </button>
        <button className="btn" style={{padding:'10px 16px'}} onClick={onClose}>{closeLabel}</button>
      </div>
    </div>
  )
}
