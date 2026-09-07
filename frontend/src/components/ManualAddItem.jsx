import React, { useState } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
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
      <div className="cgm-panel" style={{ width: 480 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>手動でアイテムを追加</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body" style={{ padding: '16px 20px' }}>
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
              onChange={e => setFiles(Array.from(e.target.files || []))}
            />
            {files.length > 0 && (
              <div style={{ fontSize: 12, color: '#6b7280', marginTop: 6 }}>
                {files.length}枚選択中: {files.map(f => f.name).join(', ')}
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
