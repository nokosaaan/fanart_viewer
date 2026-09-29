// Theme (light/dark) persistence + effective-value computation. Kept as
// plain localStorage + functions (no context/provider) to match the rest of
// this app's state style (see itemsCache.js, App.jsx's own scattered
// localStorage calls) rather than introducing a new pattern for one feature.
//
// Two independent choices, mirroring the "Default / Lights out" + "Use
// system setting" UI this was modeled on: an explicit CHOICE_KEY the user
// picks, and a USE_SYSTEM_KEY that — when on — overrides it with whatever
// prefers-color-scheme currently reports. index.html has its own tiny copy
// of computeEffectiveTheme()'s logic (see the inline script there) so the
// correct theme applies before this module (or React) ever loads.
const CHOICE_KEY = 'fv_theme_choice' // 'light' | 'dark'
const USE_SYSTEM_KEY = 'fv_theme_use_system' // 'true' | 'false'

export function getThemeChoice() {
  try { return localStorage.getItem(CHOICE_KEY) === 'dark' ? 'dark' : 'light' } catch (_) { return 'light' }
}

export function getUseSystem() {
  try { return localStorage.getItem(USE_SYSTEM_KEY) === 'true' } catch (_) { return false }
}

export function saveThemeChoice(choice) {
  try { localStorage.setItem(CHOICE_KEY, choice === 'dark' ? 'dark' : 'light') } catch (_) {}
}

export function saveUseSystem(useSystem) {
  try { localStorage.setItem(USE_SYSTEM_KEY, useSystem ? 'true' : 'false') } catch (_) {}
}

export function systemPrefersDark() {
  return typeof window !== 'undefined'
    && window.matchMedia
    && window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function computeEffectiveTheme(choice, useSystem) {
  return useSystem ? (systemPrefersDark() ? 'dark' : 'light') : choice
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light')
}
