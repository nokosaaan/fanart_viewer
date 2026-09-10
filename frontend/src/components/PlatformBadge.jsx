import React from 'react'
import { getPlatformIcon } from '../lib/platformIcon'

// Small platform badge (Twitter/Pixiv/Poipiku) shown next to a link so its
// source is recognizable at a glance without reading the URL text — see
// lib/platformIcon.js for how the platform is inferred from the link
// itself. Renders nothing when the link doesn't match a known platform
// (e.g. an unusual manually-registered link), rather than a blank/broken
// icon.
export default function PlatformBadge({ link, size = 16, style }) {
  const platform = getPlatformIcon(link)
  if (!platform) return null
  return (
    <img
      src={platform.icon} alt={platform.label} title={platform.label}
      style={{ width: size, height: size, borderRadius: 3, display: 'block', ...style }}
    />
  )
}
