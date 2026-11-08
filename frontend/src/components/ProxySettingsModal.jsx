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
    <div className="cgm-panel-backdrop" onClick={onClose} style={{ zIndex: 1600 }}>
      <div
        className="cgm-panel"
        onClick={e => e.stopPropagation()}
        style={{ width: '600px', maxWidth: '95vw', maxHeight: '90vh' }}
      >
        <div className="cgm-panel-header">
          <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>🧅</span>
            <strong>プロキシ・通信秘匿化設定 (gallery-dl)</strong>
          </span>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="cgm-panel-body" style={{ display: 'flex', flexDirection: 'column', gap: '14px', padding: '16px' }}>
          <div style={{ color: 'var(--text-faint, #6b7280)', fontSize: '0.85rem', lineHeight: '1.5' }}>
            <code>gallery-dl</code> による画像ダウンロード通信を指定のプロキシ（同梱の Tor またはカスタムプロキシ）を通して行い、IPアドレスを秘匿化します。
          </div>

          {error && (
            <div style={{ padding: '10px 12px', borderRadius: '6px', backgroundColor: '#fee2e2', color: '#991b1b', fontSize: '0.85rem' }}>
              ⚠️ {error}
            </div>
          )}

          {notice && (
            <div style={{ padding: '10px 12px', borderRadius: '6px', backgroundColor: '#dcfce7', color: '#166534', fontSize: '0.85rem' }}>
              ✓ {notice}
            </div>
          )}

          {loading ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-faint, #6b7280)' }}>設定を読み込み中...</div>
          ) : (
            <>
              {/* 有効 / 無効 スイッチ */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '12px 14px',
                  backgroundColor: 'var(--bg, #f9fafb)',
                  borderRadius: '8px',
                  border: '1px solid var(--border, #e5e7eb)',
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>プロキシ経由でダウンロード</div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-faint, #6b7280)' }}>
                    有効にすると gallery-dl に自動で <code>--proxy</code> 引数が付与されます
                  </div>
                </div>
                <label style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    className="fv-checkbox-lg"
                    checked={enabled}
                    onChange={e => setEnabled(e.target.checked)}
                    style={{ cursor: 'pointer' }}
                  />
                  <span style={{ marginLeft: '8px', fontWeight: 'bold', color: enabled ? '#2563eb' : 'var(--text-faint, #6b7280)' }}>
                    {enabled ? 'ON' : 'OFF'}
                  </span>
                </label>
              </div>

              {/* モード選択 */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <label style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text, #374151)' }}>動作モード</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                  <div
                    onClick={() => setMode('tor_auto')}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: `2px solid ${mode === 'tor_auto' ? '#3b82f6' : 'var(--border, #e5e7eb)'}`,
                      backgroundColor: mode === 'tor_auto' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg, #f9fafb)',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.9rem' }}>
                      <span>🧅</span> Tor 自動起動 <span style={{ fontSize: '0.75rem', color: '#2563eb' }}>(推奨)</span>
                    </div>
                    <div style={{ fontSize: '0.76rem', color: 'var(--text-faint, #6b7280)', marginTop: '4px', lineHeight: '1.4' }}>
                      ダウンロード時のみ裏で Tor を起動し、終了後に自動停止します。ブラウザ起動は不要です。
                    </div>
                  </div>

                  <div
                    onClick={() => setMode('custom')}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: `2px solid ${mode === 'custom' ? '#3b82f6' : 'var(--border, #e5e7eb)'}`,
                      backgroundColor: mode === 'custom' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg, #f9fafb)',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.9rem' }}>
                      <span>⚙️</span> カスタムプロキシ
                    </div>
                    <div style={{ fontSize: '0.76rem', color: 'var(--text-faint, #6b7280)', marginTop: '4px', lineHeight: '1.4' }}>
                      起動済みの Tor Browser (9150) や WARP (40000)、外部 SOCKS5/HTTP プロキシを指定します。
                    </div>
                  </div>
                </div>
              </div>

              {/* モードごとの詳細設定 */}
              {mode === 'tor_auto' ? (
                <div
                  style={{
                    padding: '12px 14px',
                    backgroundColor: 'var(--bg, #f9fafb)',
                    borderRadius: '8px',
                    border: '1px solid var(--border, #e5e7eb)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.82rem' }}>
                    <span style={{ color: 'var(--text-faint, #6b7280)' }}>Tor コマンド検知:</span>
                    {status?.tor_installed ? (
                      <span style={{ color: '#16a34a', fontWeight: 600 }}>
                        ✓ 検出済み ({status.tor_binary_path})
                      </span>
                    ) : (
                      <span style={{ color: '#dc2626', fontWeight: 600 }}>
                        ⚠️ 未検出 (PATH または tor_win に tor.exe が必要です)
                      </span>
                    )}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                    <div>
                      <label style={{ fontSize: '0.78rem', color: 'var(--text-faint, #6b7280)', display: 'block', marginBottom: '3px' }}>
                        待受ポート (SocksPort)
                      </label>
                      <input
                        type="number"
                        className="cgm-search-input"
                        value={torPort}
                        onChange={e => setTorPort(e.target.value)}
                        placeholder="9052"
                        style={{ fontSize: '0.85rem' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.78rem', color: 'var(--text-faint, #6b7280)', display: 'block', marginBottom: '3px' }}>
                        アイドル停止待機 (秒)
                      </label>
                      <input
                        type="number"
                        className="cgm-search-input"
                        value={idleTimeout}
                        onChange={e => setIdleTimeout(e.target.value)}
                        placeholder="60"
                        style={{ fontSize: '0.85rem' }}
                      />
                    </div>
                  </div>

                  <div>
                    <label style={{ fontSize: '0.78rem', color: 'var(--text-faint, #6b7280)', display: 'block', marginBottom: '3px' }}>
                      Tor 実行ファイルパス (空欄で同梱 tor.exe または PATH から自動検出)
                    </label>
                    <input
                      type="text"
                      className="cgm-search-input"
                      value={torPath}
                      onChange={e => setTorPath(e.target.value)}
                      placeholder="未指定 (自動検出)"
                      style={{ fontSize: '0.85rem' }}
                    />
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    padding: '12px 14px',
                    backgroundColor: 'var(--bg, #f9fafb)',
                    borderRadius: '8px',
                    border: '1px solid var(--border, #e5e7eb)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div>
                    <label style={{ fontSize: '0.78rem', color: 'var(--text-faint, #6b7280)', display: 'block', marginBottom: '3px' }}>
                      プロキシ URL
                    </label>
                    <input
                      type="text"
                      className="cgm-search-input"
                      value={customProxyUrl}
                      onChange={e => setCustomProxyUrl(e.target.value)}
                      placeholder="socks5h://127.0.0.1:9150"
                      style={{ fontSize: '0.85rem' }}
                    />
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-faint, #6b7280)', marginTop: '4px' }}>
                      💡 Tor Browser は <code>socks5h://127.0.0.1:9150</code>、WARP は <code>socks5h://127.0.0.1:40000</code> です。
                    </div>
                  </div>
                </div>
              )}

              {/* 接続テスト結果 */}
              {testResult && (
                <div
                  style={{
                    padding: '10px 12px',
                    borderRadius: '6px',
                    backgroundColor: testResult.success ? '#ecfdf5' : '#fef2f2',
                    border: `1px solid ${testResult.success ? '#10b981' : '#f87171'}`,
                    fontSize: '0.82rem',
                  }}
                >
                  {testResult.success ? (
                    <div>
                      <div style={{ color: '#047857', fontWeight: 600, marginBottom: '2px' }}>
                        ✓ プロキシ接続成功！
                      </div>
                      <div style={{ color: 'var(--text, #374151)' }}>
                        認識された外部 IP: <strong style={{ color: '#2563eb' }}>{testResult.ip}</strong>
                      </div>
                      <div style={{ color: 'var(--text-faint, #6b7280)', fontSize: '0.75rem', marginTop: '2px' }}>
                        応答時間: {testResult.elapsed_seconds} 秒 ({testResult.proxy_used})
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: '#b91c1c' }}>
                      ❌ 接続エラー: {testResult.error}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid var(--divider, #e5e7eb)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexShrink: 0,
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={handleTest}
            disabled={testing || saving || (mode === 'tor_auto' && !status?.tor_installed)}
            style={{ fontSize: 13 }}
          >
            {testing ? '接続テスト中...' : '🔍 接続テスト'}
          </button>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="button" className="btn" onClick={onClose} disabled={saving} style={{ fontSize: 13 }}>
              キャンセル
            </button>
            <button
              type="button"
              className="btn"
              onClick={handleSave}
              disabled={saving || loading}
              style={{ background: '#3b82f6', color: '#fff', fontSize: 13 }}
            >
              {saving ? '保存中...' : '設定を保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
