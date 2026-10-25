import React, { useState } from 'react'
import { sortFilesNaturally } from '../lib/naturalSort'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*([^;]+)')
  return m ? m.pop() : ''
}

// For when the original post's URL is dead (deleted/suspended/404) but the
// image was already saved locally before that happened — creates a bare
// Item straight from uploaded file(s), bypassing the whole URL-fetch
// pipeline entirely (see ItemViewSet.create_manual). Deliberately doesn't
// ask for titles/characters/tags/situation here — the newly created item
// gets handed straight to the normal edit form (see App.jsx's onCreated),
// which already has AI suggestion, character picking, etc., built in.
export default function ManualAddItem({ onClose, onCreated }) {
  const [files, setFiles] = useState([])
  const [link, setLink] = useState('')
  const [artist, setArtist] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  function handleFileChange(e) {
    const rawFiles = Array.from(e.target.files || [])
    // Automatically sort files naturally so numbered manga pages (e.g. 05.jpg, 21.jpg)
    // are ordered correctly even if selected in random order by the OS file dialog.
    setFiles(sortFilesNaturally(rawFiles))
  }

  function sortFiles(reverse = false) {
    setFiles(prev => sortFilesNaturally(prev, reverse))
  }

  function moveFile(idx, direction) {
    const newIdx = idx + direction
    if (newIdx < 0 || newIdx >= files.length) return
    const next = [...files]
    const [moved] = next.splice(idx, 1)
    next.splice(newIdx, 0, moved)
    setFiles(next)
  }

  function removeFile(idx) {
    setFiles(prev => prev.filter((_, i) => i !== idx))
  }

  async function create() {
    if (files.length === 0) { setError('画像ファイルを1枚以上選択してください'); return }
    setCreating(true)
    setError('')
    try {
      const formData = new FormData()
      for (const f of files) formData.append('images', f)
      if (link.trim()) formData.append('link', link.trim())
      if (artist.trim()) formData.append('artist', artist.trim())

      // No 'Content-Type' header here — the browser sets
      // multipart/form-data with the correct boundary itself; setting it
      // manually would break the upload.
      const resp = await fetch('/api/items/create_manual/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        credentials: 'same-origin',
        body: formData,
      })
      const j = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(j.detail || `作成に失敗しました (${resp.status})`)
      onCreated(j.item)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 520, maxWidth: '95vw' }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>手動でアイテムを追加</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body" style={{ padding: '16px 20px', maxHeight: '80vh', overflowY: 'auto' }}>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 16 }}>
            元投稿のURLが死んでいる(削除・凍結・404など)が、画像は手元に保存してある場合に使います。
            URLへの取得は一切行わず、選択した画像ファイルをそのままアイテムとして登録します。
            登録後はいつも通りの編集画面が開くので、タイトル・キャラクター等はそこで設定してください。
          </div>

          {error && <div style={{ color: '#f87171', marginBottom: 12, fontSize: 13 }}>{error}</div>}

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#6b7280', marginBottom: 4 }}>画像ファイル(複数選択可)</label>
            <input
              type="file" accept="image/*" multiple
              onChange={handleFileChange}
            />
            {files.length > 0 && (
              <div style={{ marginTop: 8, background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#334155' }}>
                    {files.length}枚選択中 (番号順に自動整列済み):
                  </span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button type="button" className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => sortFiles(false)} title="ファイル名の自然順(01, 02... 10)で整列">
                      名前順 ↑
                    </button>
                    <button type="button" className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => sortFiles(true)} title="ファイル名の逆順で整列">
                      名前順 ↓
                    </button>
                  </div>
                </div>
                <div style={{ maxHeight: 160, overflowY: 'auto', fontSize: 12, background: '#fff', border: '1px solid #cbd5e1', borderRadius: 4, padding: 4 }}>
                  {files.map((f, i) => (
                    <div key={`${f.name}-${i}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 6px', borderBottom: i < files.length - 1 ? '1px solid #f1f5f9' : 'none' }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 350 }} title={f.name}>
                        <strong style={{ color: '#64748b', marginRight: 6 }}>{i + 1}.</strong>
                        <span style={{ color: '#0f172a' }}>{f.name}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                        <button type="button" style={{ border: 'none', background: 'transparent', cursor: i === 0 ? 'default' : 'pointer', opacity: i === 0 ? 0.3 : 1, padding: '0 4px', fontSize: 11 }}
                          disabled={i === 0} onClick={() => moveFile(i, -1)} title="前へ">▲</button>
                        <button type="button" style={{ border: 'none', background: 'transparent', cursor: i === files.length - 1 ? 'default' : 'pointer', opacity: i === files.length - 1 ? 0.3 : 1, padding: '0 4px', fontSize: 11 }}
                          disabled={i === files.length - 1} onClick={() => moveFile(i, 1)} title="次へ">▼</button>
                        <button type="button" style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#ef4444', padding: '0 4px', fontSize: 11 }}
                          onClick={() => removeFile(i)} title="削除">✕</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#6b7280', marginBottom: 4 }}>元URL(任意・死んでいてもOK、記録用)</label>
            <input
              style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 6 }}
              value={link} onChange={e => setLink(e.target.value)}
              placeholder="https://x.com/.../status/..."
            />
          </div>

          <div style={{ marginBottom: 18 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#6b7280', marginBottom: 4 }}>作者(任意)</label>
            <input
              style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 6 }}
              value={artist} onChange={e => setArtist(e.target.value)}
              placeholder="作者名 / Twitter ID"
            />
          </div>

          <button className="btn" style={{ background: '#3b82f6', color: '#fff', padding: '9px 20px', fontWeight: 600 }}
            onClick={create} disabled={creating}>
            {creating ? '作成中…' : '作成して編集を開く'}
          </button>
        </div>
      </div>
    </div>
  )
}
