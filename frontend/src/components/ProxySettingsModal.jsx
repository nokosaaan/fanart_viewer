import React, { useState, useEffect, useCallback } from 'react'

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

const HEADERS = { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }

export default function ProxySettingsModal({ onClose }) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [testResult, setTestResult] = useState(null)

  // Form states
  const [enabled, setEnabled] = useState(false)
  const [mode, setMode] = useState('tor_auto')
  const [torPath, setTorPath] = useState('')
  const [torPort, setTorPort] = useState(9052)
  const [customProxyUrl, setCustomProxyUrl] = useState('socks5h://127.0.0.1:9150')
  const [idleTimeout, setIdleTimeout] = useState(60)

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/proxy_settings/', { credentials: 'same-origin' })
      const data = await r.json()
      if (!r.ok) throw new Error(data.error || `設定の取得に失敗しました (${r.status})`)
      setStatus(data)
      const c = data.config || {}
      setEnabled(!!c.enabled)
      setMode(c.mode || 'tor_auto')
      setTorPath(c.tor_path || '')
      setTorPort(c.tor_port || 9052)
      setCustomProxyUrl(c.custom_proxy_url || 'socks5h://127.0.0.1:9150')
      setIdleTimeout(c.idle_timeout_seconds || 60)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  async function handleSave() {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const payload = {
        enabled,
        mode,
        tor_path: torPath.trim(),
        tor_port: parseInt(torPort, 10) || 9052,
        custom_proxy_url: customProxyUrl.trim(),
        idle_timeout_seconds: parseInt(idleTimeout, 10) || 60,
      }
      const r = await fetch('/api/proxy_settings/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.error || `保存に失敗しました (${r.status})`)
      setStatus(data)
      setNotice('設定を保存しました')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    setError('')
    setNotice('')
    setTestResult(null)
    try {
      let payload = {}
      if (mode === 'custom') {
        payload = { proxy_url: customProxyUrl.trim() }
      }
      // If testing tor_auto, backend will temporarily spin up Tor
      const r = await fetch('/api/proxy_settings/test/', {
        method: 'POST',
        headers: HEADERS,
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      })
      const data = await r.json()
      setTestResult(data)
      if (!data.success) {
        setError(data.error || '接続テストに失敗しました')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} style={{ zIndex: 1050 }}>
      <div
        className="modal-content"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: '640px', width: '92%', maxHeight: '90vh', overflowY: 'auto' }}
      >
        <div className="modal-top">
          <h2 style={{ fontSize: '1.2rem', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🧅</span> プロキシ・通信秘匿化設定 (gallery-dl)
          </h2>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-main" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ color: '#9ca3af', fontSize: '0.88rem', lineHeight: '1.5' }}>
            <code>gallery-dl</code> を経由する画像のダウンロード通信を指定のプロキシ（Tor またはカスタムプロキシ）を通して行い、自宅IPアドレスを秘匿化します。
          </div>

          {error && (
            <div style={{ padding: '10px 14px', borderRadius: '6px', backgroundColor: '#7f1d1d', color: '#fecaca', fontSize: '0.88rem' }}>
              ⚠️ {error}
            </div>
          )}

          {notice && (
            <div style={{ padding: '10px 14px', borderRadius: '6px', backgroundColor: '#064e3b', color: '#a7f3d0', fontSize: '0.88rem' }}>
              ✓ {notice}
            </div>
          )}

          {loading ? (
            <div style={{ padding: '24px', textAlign: 'center', color: '#9ca3af' }}>設定を読み込み中...</div>
          ) : (
            <>
              {/* 有効 / 無効 スイッチ */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '12px 16px',
                  backgroundColor: '#1f2937',
                  borderRadius: '8px',
                  border: '1px solid #374151',
                }}
              >
                <div>
                  <div style={{ fontWeight: 'bold', fontSize: '0.95rem' }}>プロキシ経由でダウンロード</div>
                  <div style={{ fontSize: '0.8rem', color: '#9ca3af' }}>
                    有効にすると gallery-dl に自動で <code>--proxy</code> 引数が付与されます
                  </div>
                </div>
                <label style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={e => setEnabled(e.target.checked)}
                    style={{ width: '20px', height: '20px', accentColor: '#3b82f6', cursor: 'pointer' }}
                  />
                  <span style={{ marginLeft: '8px', fontWeight: 'bold', color: enabled ? '#60a5fa' : '#9ca3af' }}>
                    {enabled ? 'ON' : 'OFF'}
                  </span>
                </label>
              </div>

              {/* モード選択 */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <label style={{ fontSize: '0.88rem', fontWeight: 'bold', color: '#d1d5db' }}>動作モード</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                  <div
                    onClick={() => setMode('tor_auto')}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: `2px solid ${mode === 'tor_auto' ? '#3b82f6' : '#374151'}`,
                      backgroundColor: mode === 'tor_auto' ? '#1e3a8a33' : '#1f2937',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span>🧅</span> Tor 自動起動 <span style={{ fontSize: '0.75rem', color: '#60a5fa' }}>(推奨)</span>
                    </div>
                    <div style={{ fontSize: '0.78rem', color: '#9ca3af', marginTop: '6px', lineHeight: '1.4' }}>
                      ダウンロード時のみ裏で Tor を起動し、終了後に自動停止します。ブラウザ起動は不要です。
                    </div>
                  </div>

                  <div
                    onClick={() => setMode('custom')}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: `2px solid ${mode === 'custom' ? '#3b82f6' : '#374151'}`,
                      backgroundColor: mode === 'custom' ? '#1e3a8a33' : '#1f2937',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span>⚙️</span> カスタムプロキシ
                    </div>
                    <div style={{ fontSize: '0.78rem', color: '#9ca3af', marginTop: '6px', lineHeight: '1.4' }}>
                      起動済みの Tor Browser (9150) や WARP (40000)、外部 SOCKS5/HTTP プロキシを指定します。
                    </div>
                  </div>
                </div>
              </div>

              {/* モードごとの詳細設定 */}
              {mode === 'tor_auto' ? (
                <div
                  style={{
                    padding: '14px',
                    backgroundColor: '#111827',
                    borderRadius: '8px',
                    border: '1px solid #374151',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  {/* システムの Tor 検知状況 */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.85rem' }}>
                    <span style={{ color: '#9ca3af' }}>Tor コマンド検知:</span>
                    {status?.tor_installed ? (
                      <span style={{ color: '#34d399', fontWeight: 'bold' }}>
                        ✓ 検出済み ({status.tor_binary_path})
                      </span>
                    ) : (
                      <span style={{ color: '#f87171', fontWeight: 'bold' }}>
                        ⚠️ 未検出 (ターミナルで <code>sudo apt install tor</code> を実行してください)
                      </span>
                    )}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: '#9ca3af', display: 'block', marginBottom: '4px' }}>
                        待受ポート (SocksPort)
                      </label>
                      <input
                        type="number"
                        className="form-control"
                        value={torPort}
                        onChange={e => setTorPort(e.target.value)}
                        placeholder="9052"
                        style={{ width: '100%', fontSize: '0.88rem' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: '#9ca3af', display: 'block', marginBottom: '4px' }}>
                        アイドル停止待機 (秒)
                      </label>
                      <input
                        type="number"
                        className="form-control"
                        value={idleTimeout}
                        onChange={e => setIdleTimeout(e.target.value)}
                        placeholder="60"
                        style={{ width: '100%', fontSize: '0.88rem' }}
                      />
                    </div>
                  </div>

                  <div>
                    <label style={{ fontSize: '0.8rem', color: '#9ca3af', display: 'block', marginBottom: '4px' }}>
                      Tor 実行ファイルパス (空欄で PATH から自動検出)
                    </label>
                    <input
                      type="text"
                      className="form-control"
                      value={torPath}
                      onChange={e => setTorPath(e.target.value)}
                      placeholder="/usr/bin/tor"
                      style={{ width: '100%', fontSize: '0.88rem' }}
                    />
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    padding: '14px',
                    backgroundColor: '#111827',
                    borderRadius: '8px',
                    border: '1px solid #374151',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  <div>
                    <label style={{ fontSize: '0.8rem', color: '#9ca3af', display: 'block', marginBottom: '4px' }}>
                      プロキシ URL (例: <code>socks5h://127.0.0.1:9150</code>)
                    </label>
                    <input
                      type="text"
                      className="form-control"
                      value={customProxyUrl}
                      onChange={e => setCustomProxyUrl(e.target.value)}
                      placeholder="socks5h://127.0.0.1:9150"
                      style={{ width: '100%', fontSize: '0.88rem' }}
                    />
                    <div style={{ fontSize: '0.75rem', color: '#6b7280', marginTop: '4px' }}>
                      💡 Tor Browser は <code>socks5h://127.0.0.1:9150</code>、WARP は <code>socks5h://127.0.0.1:40000</code> です。
                      DNSリークを防ぐため <code>socks5h://</code> の利用を推奨します。
                    </div>
                  </div>
                </div>
              )}

              {/* 接続テスト結果 */}
              {testResult && (
                <div
                  style={{
                    padding: '12px 14px',
                    borderRadius: '6px',
                    backgroundColor: testResult.success ? '#064e3b33' : '#7f1d1d33',
                    border: `1px solid ${testResult.success ? '#059669' : '#dc2626'}`,
                    fontSize: '0.85rem',
                  }}
                >
                  {testResult.success ? (
                    <div>
                      <div style={{ color: '#34d399', fontWeight: 'bold', marginBottom: '4px' }}>
                        ✓ プロキシ接続成功！
                      </div>
                      <div style={{ color: '#d1d5db' }}>
                        認識された外部 IP: <strong style={{ color: '#60a5fa' }}>{testResult.ip}</strong>
                      </div>
                      <div style={{ color: '#9ca3af', fontSize: '0.78rem', marginTop: '2px' }}>
                        応答時間: {testResult.elapsed_seconds} 秒 ({testResult.proxy_used})
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: '#f87171' }}>
                      ❌ 接続エラー: {testResult.error}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div
          className="modal-footer"
          style={{
            padding: '14px 16px',
            borderTop: '1px solid #374151',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleTest}
            disabled={testing || saving || (mode === 'tor_auto' && !status?.tor_installed)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
          >
            {testing ? '接続テスト中...' : '🔍 接続テスト'}
          </button>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={saving}>
              キャンセル
            </button>
            <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving || loading}>
              {saving ? '保存中...' : '設定を保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
