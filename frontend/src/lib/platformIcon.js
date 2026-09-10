// Maps an item's link URL to a small platform badge icon + label, so a
// row/preview can show at a glance which site a post came from without
// reading the URL text itself (twitter.com/x.com, pixiv.net, poipiku.com).
// Goes by the link's own domain rather than Item.source -- a manually
// registered item (source='manual') still has a real platform link worth
// badging, and Poipiku items never get a dedicated source value at all
// (see item/poipiku_fetch.py), so source alone would miss them.
const PLATFORMS = [
  { test: /(?:^|\.)(?:twitter|x)\.com\//i, icon: '/icons/twitter.svg', label: 'Twitter/X' },
  { test: /(?:^|\.)pixiv\.net\//i, icon: '/icons/pixiv.svg', label: 'Pixiv' },
  { test: /(?:^|\.)poipiku\.com\//i, icon: '/icons/poipiku.svg', label: 'Poipiku' },
]

export function getPlatformIcon(url) {
  if (!url) return null
  for (const p of PLATFORMS) {
    if (p.test.test(url)) return { icon: p.icon, label: p.label }
  }
  return null
}
