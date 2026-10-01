import React from 'react'
import { SunIcon, MoonIcon, MonitorIcon } from './MenuIcons'

// Modal for picking the app's color theme — a three-way "ライトモード /
// ダークモード / デバイスのデフォルト" list (same shape as GitHub's own
// appearance menu), rather than separate light/dark swatches plus an
// unrelated "follow the OS setting" checkbox. Persistence and the actual
// effective-theme computation still live in lib/theme.js (and index.html's
// own copy, applied before this ever mounts) — this component only reads/
// writes those same two localStorage-backed choices: `useSystem` picks the
// third row, and `choice` (light/dark) picks between the first two whenever
// `useSystem` is off.
const APPEARANCE_OPTIONS = [
  { key: 'light', label: 'ライトモード', Icon: SunIcon },
  { key: 'dark', label: 'ダークモード', Icon: MoonIcon },
  { key: 'system', label: 'デバイスのデフォルト', Icon: MonitorIcon },
]

export default function ThemeSettings({ choice, useSystem, onChoice, onUseSystemChange, defaultViewMode, onDefaultViewModeChange, onClose }) {
  const selected = useSystem ? 'system' : choice

  function selectAppearance(key) {
    if (key === 'system') {
      onUseSystemChange(true)
      return
    }
    onUseSystemChange(false)
    onChoice(key)
  }

  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>表示設定</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="cgm-panel-body">
          <div className="appearance-list">
            {APPEARANCE_OPTIONS.map(({ key, label, Icon }) => (
              <button
                key={key}
                type="button"
                className={`appearance-option${selected === key ? ' selected' : ''}`}
                onClick={() => selectAppearance(key)}
              >
                <span className="appearance-option-icon"><Icon /></span>
                <span className="appearance-option-label">{label}</span>
                {selected === key && <span className="appearance-option-check">✓</span>}
              </button>
            ))}
          </div>

          <div className="theme-system-row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
            <strong style={{ fontSize: 13 }}>起動時の表示形式</strong>
            <div className="theme-options" style={{ marginTop: 10 }}>
              <button
                type="button"
                className={`theme-option${defaultViewMode === 'list' ? ' selected' : ''}`}
                onClick={() => onDefaultViewModeChange('list')}
              >
                {defaultViewMode === 'list' && <span className="theme-option-check">✓</span>}
                <span className="theme-option-label">リスト</span>
              </button>
              <button
                type="button"
                className={`theme-option${defaultViewMode === 'gallery' ? ' selected' : ''}`}
                onClick={() => onDefaultViewModeChange('gallery')}
              >
                {defaultViewMode === 'gallery' && <span className="theme-option-check">✓</span>}
                <span className="theme-option-label">ギャラリー</span>
              </button>
            </div>
          </div>
          <div className="theme-system-hint" style={{ margin: '8px 0 0' }}>
            次回の起動時にどちらの表示形式で開くかを選べます(画面上部のリスト/ギャラリー切り替えは、この回だけの一時的な切り替えです)。
          </div>
        </div>
      </div>
    </div>
  )
}
