// Perceptual color naming + pixel sampling helper for inspecting character hair/eye/outfit colors.

export function rgbToHex(r, g, b) {
  const toHex = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0').toUpperCase()
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

export function rgbToHsv(r, g, b) {
  const rn = r / 255
  const gn = g / 255
  const bn = b / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const d = max - min

  let h = 0
  if (d !== 0) {
    if (max === rn) h = ((gn - bn) / d) % 6
    else if (max === gn) h = (bn - rn) / d + 2
    else h = (rn - gn) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  const s = max === 0 ? 0 : (d / max) * 100
  const v = max * 100
  return { h, s, v }
}

/**
 * Returns a human-friendly Japanese color name approximating how the RGB color
 * looks to the human eye, along with HEX / RGB / HSV values.
 */
export function describePerceivedColor(r, g, b) {
  const hex = rgbToHex(r, g, b)
  const { h, s, v } = rgbToHsv(r, g, b)
  const rgb = `rgb(${r}, ${g}, ${b})`

  let label = ''

  // 1. Very dark / achromatic colors
  if (v < 13) {
    label = '黒'
  } else if (s < 11) {
    if (v >= 90) label = '白'
    else if (v >= 68) label = '明るいグレー（銀系）'
    else if (v >= 38) label = 'グレー'
    else label = '暗いグレー（チャコール）'
  } else if (s < 20 && v >= 85 && (h < 50 || h >= 330)) {
    // Faintly tinted off-white / silver-pink / ivory
    if (h >= 25 && h < 55) label = 'アイボリー / クリーム白'
    else if (h >= 330 || h < 25) label = 'ごく薄いピンク / オフホワイト'
    else label = '白っぽいグレー'
  } else if (s < 22 && v < 40) {
    label = '暗いグレー'
  } else if (h >= 12 && h < 45 && v >= 68 && s < 48) {
    // Skin tones / beige / light brown
    if (v >= 82 && s < 35) label = '肌色 / クリーム'
    else label = 'ベージュ / 薄い茶色'
  } else if (h >= 8 && h < 48 && v < 62) {
    // Browns
    if (v < 30) label = '焦げ茶'
    else if (h < 18 && s >= 40) label = '赤茶'
    else label = '茶色'
  } else if (h >= 342 || h < 12) {
    // Reds & Pinks
    if (v >= 72 && s < 55) label = 'ピンク'
    else if (v >= 85 && s < 32) label = '薄いピンク'
    else if (v < 45) label = '暗い赤（えんじ）'
    else label = '赤'
  } else if (h >= 12 && h < 38) {
    // Oranges
    if (v >= 78 && s < 45) label = 'アプリコット / 肌色'
    else if (v < 58) label = '茶色'
    else label = 'オレンジ'
  } else if (h >= 38 && h < 66) {
    // Yellows
    if (v >= 85 && s < 35) label = 'クリーム色（淡い黄）'
    else if (v < 55) label = 'からし色 / オリーブ'
    else label = '黄色（金系）'
  } else if (h >= 66 && h < 95) {
    // Yellow-green
    if (v < 48) label = '暗い黄緑 / オリーブ'
    else label = '黄緑'
  } else if (h >= 95 && h < 155) {
    // Greens
    if (v >= 75 && s < 40) label = 'ミント / 薄い緑'
    else if (v < 40) label = '深緑'
    else label = '緑'
  } else if (h >= 155 && h < 185) {
    // Cyan-green / Teal
    if (v < 45) label = '深い青緑'
    else if (v >= 78 && s < 40) label = '薄い水色（ミント）'
    else label = '青緑（エメラルド）'
  } else if (h >= 185 && h < 212) {
    // Light blues / Cyan
    if (v < 45) label = '暗い青'
    else if (s < 32 && v >= 75) label = '薄い水色'
    else label = '水色'
  } else if (h >= 212 && h < 252) {
    // Blues
    if (v < 38) label = '紺色（ネイビー）'
    else if (v >= 75 && s < 40) label = '薄い青（空色）'
    else label = '青'
  } else if (h >= 252 && h < 295) {
    // Purples
    if (v >= 70 && s < 45) label = 'ラベンダー / 薄い紫'
    else if (v < 42) label = '濃い紫'
    else label = '紫'
  } else {
    // 295 - 342: Magenta / Rose / Pink
    if (v >= 72 && s < 65) label = 'ピンク'
    else if (v < 45) label = '濃い赤紫（ワイン）'
    else label = '赤紫（マゼンタ）'
  }

  return {
    label,
    hex,
    rgb,
    r,
    g,
    b,
    hsv: { h: Math.round(h), s: Math.round(s), v: Math.round(v) },
  }
}

/**
 * Creates a reusable pixel sampler backed by an offscreen canvas for an <img>.
 * Rebuilds automatically if the image dimensions change.
 */
export function createImagePixelSampler() {
  let canvas = null
  let ctx = null
  let cachedSrc = ''
  let cachedW = 0
  let cachedH = 0

  function ensureCanvas(img) {
    if (!img || !img.complete || !img.naturalWidth || !img.naturalHeight) return false
    const w = img.naturalWidth
    const h = img.naturalHeight
    const src = img.currentSrc || img.src || ''
    if (canvas && cachedSrc === src && cachedW === w && cachedH === h) return true

    canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return false
    try {
      ctx.drawImage(img, 0, 0, w, h)
      cachedSrc = src
      cachedW = w
      cachedH = h
      return true
    } catch (_) {
      canvas = null
      ctx = null
      return false
    }
  }

  function sampleAt(img, naturalX, naturalY) {
    if (!ensureCanvas(img)) return null
    const x = Math.max(0, Math.min(cachedW - 1, Math.round(naturalX)))
    const y = Math.max(0, Math.min(cachedH - 1, Math.round(naturalY)))
    try {
      const data = ctx.getImageData(x, y, 1, 1).data
      return {
        ...describePerceivedColor(data[0], data[1], data[2]),
        a: data[3],
        x,
        y,
        width: cachedW,
        height: cachedH,
      }
    } catch (_) {
      return null
    }
  }

  function getCanvas(img) {
    return ensureCanvas(img) ? canvas : null
  }

  return { sampleAt, getCanvas }
}
