import React, { useState } from 'react'

export default function LoginScreen({ onLogin, isAdmin = false }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const endpoint = isAdmin ? '/api/auth/admin/' : '/api/auth/'
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const j = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(j.detail || 'Invalid password')
        setLoading(false)
        return
      }
      localStorage.setItem('fv_token', j.token)
      localStorage.setItem('fv_role', j.role)
      onLogin(j.role)
    } catch (e) {
      setError('接続エラー')
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0f172a',
    }}>
      <form onSubmit={submit} style={{
        background: '#1e293b', padding: '36px 44px', borderRadius: 12,
        minWidth: 320, display: 'flex', flexDirection: 'column', gap: 16,
        boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
      }}>
        <h2 style={{ color: '#f8fafc', margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: 0.5 }}>
          fanart viewer
          {isAdmin && <span style={{ fontSize: 13, color: '#a78bfa', marginLeft: 8 }}>管理者</span>}
        </h2>
        <input
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder="パスワードを入力"
          autoFocus
          style={{
            padding: '10px 14px', borderRadius: 6,
            border: error ? '1px solid #f87171' : '1px solid #334155',
            background: '#0f172a', color: '#f8fafc', fontSize: 14,
            outline: 'none', transition: 'border-color 0.15s',
          }}
        />
        {error && (
          <div style={{ color: '#f87171', fontSize: 13, marginTop: -8 }}>{error}</div>
        )}
        <button
          type="submit"
          disabled={loading || !password}
          style={{
            padding: '10px', borderRadius: 6, border: 'none',
            background: isAdmin ? '#7c3aed' : '#3b82f6', color: '#fff', fontSize: 14, fontWeight: 500,
            cursor: loading || !password ? 'default' : 'pointer',
            opacity: loading || !password ? 0.6 : 1,
            transition: 'opacity 0.15s',
          }}
        >
          {loading ? 'ログイン中…' : 'ログイン'}
        </button>
      </form>
    </div>
  )
}
