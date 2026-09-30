// Persisted default for App.jsx's viewMode ('list' | 'gallery') — separate
// from viewMode's own in-session state so switching views with the header
// toggle is just a session-local look, and only the "表示設定" panel
// (ThemeSettings.jsx) actually changes what a fresh launch starts on.
const KEY = 'fv_default_view_mode'

export function getDefaultViewMode() {
  try { return localStorage.getItem(KEY) === 'list' ? 'list' : 'gallery' } catch (_) { return 'gallery' }
}

export function saveDefaultViewMode(mode) {
  try { localStorage.setItem(KEY, mode === 'list' ? 'list' : 'gallery') } catch (_) {}
}
