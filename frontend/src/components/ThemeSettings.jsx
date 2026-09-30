import React from 'react'

// Modal for picking the app's color theme — "Default" (light) / "Lights
// out" (dark), plus a "follow the OS setting" override. Persistence and the
// actual effective-theme computation live in lib/theme.js (and index.html's
// own copy, applied before this ever mounts) — this component only reads/
// writes those same two localStorage-backed choices.
export default function ThemeSettings({ choice, useSystem, onChoice, onUseSystemChange, defaultViewMode, onDefaultViewModeChange, onClose }) {
  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>表示設定</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="cgm-panel-body">
          <div className="theme-options">
            <button
              type="button"
              className={`theme-option${!useSystem && choice === 'light' ? ' selected' : ''}`}
              onClick={() => onChoice('light')}
            >
              {!useSystem && choice === 'light' && <span className="theme-option-check">✓</span>}
              <span className="theme-option-preview theme-preview-light" />
              <span className="theme-option-label">Default</span>
            </button>
            <button
              type="button"
              className={`theme-option${!useSystem && choice === 'dark' ? ' selected' : ''}`}
              onClick={() => onChoice('dark')}
            >
              {!useSystem && choice === 'dark' && <span className="theme-option-check">✓</span>}
              <span className="theme-option-preview theme-preview-dark" />
              <span className="theme-option-label">Lights out</span>
            </button>
          </div>

          <div className="theme-system-row">
            <label>
              <input
                type="checkbox"
                checked={useSystem}
                onChange={e => onUseSystemChange(e.target.checked)}
              />
              端末の設定に合わせる
            </label>
          </div>
          <div className="theme-system-hint">
            オンにすると、上の選択より端末(OS)のダーク/ライト設定を優先します。
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
