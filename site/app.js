const REPO = 'nokosaaan/fanart_viewer'
const API_BASE = `https://api.github.com/repos/${REPO}`

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

// Minimal, dependency-free markdown -> HTML for exactly what GitHub's
// auto-generated release notes actually contain (## headings, * bullets,
// **bold**, [text](url) links) -- escapes first, so nothing from the API
// response is ever inserted as raw HTML.
function renderNotes(markdown) {
  if (!markdown) return ''
  const escaped = escapeHtml(markdown)
  const withLinks = escaped.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
  const withBold = withLinks.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  const lines = withBold.split('\n').map(line => {
    const heading = line.match(/^#{1,3}\s+(.*)/)
    if (heading) return `<div style="font-weight:500;margin-top:8px">${heading[1]}</div>`
    const bullet = line.match(/^\*\s+(.*)/)
    if (bullet) return `– ${bullet[1]}`
    return line
  })
  return lines.join('\n')
}

function formatBytes(bytes) {
  if (!bytes) return ''
  const mb = bytes / (1024 * 1024)
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`
}

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('ja-JP', { year: 'numeric', month: 'long', day: 'numeric' })
}

function findZipAsset(release) {
  return (release.assets || []).find(a => a.name.toLowerCase().endsWith('.zip')) || null
}

function renderReleaseCard(release, isLatest) {
  const badge = isLatest ? '<span class="release-badge">最新</span>'
    : release.prerelease ? '<span class="release-badge">プレリリース</span>' : ''

  const linksHtml = (release.assets || []).length > 0
    ? `<div class="release-links">${release.assets.map(a => `
        <a class="release-asset-link" href="${a.browser_download_url}">
          ${escapeHtml(a.name)} <span class="release-asset-size">(${formatBytes(a.size)})</span> →
        </a>`).join('')}</div>`
    : `<div class="release-empty">このバージョンのビルドはまだアップロードされていません。</div>`

  const notesHtml = release.body && release.body.trim()
    ? `<details class="release-notes"><summary>変更内容を見る →</summary><div class="release-notes-body">${renderNotes(release.body)}</div></details>`
    : ''

  return `
    <div class="release-card${isLatest ? ' is-latest' : ''}">
      <div class="release-head">
        <span class="release-tag">${escapeHtml(release.tag_name)}</span>
        ${badge}
        <span class="release-date">${formatDate(release.published_at || release.created_at)}</span>
      </div>
      ${linksHtml}
      ${notesHtml}
    </div>`
}

async function loadReleases() {
  const listEl = document.getElementById('release-list')
  const errorEl = document.getElementById('release-error')
  const primaryLabel = document.getElementById('primary-download-label')
  const primaryMeta = document.getElementById('primary-download-meta')
  const primaryLink = document.getElementById('primary-download')

  try {
    const resp = await fetch(`${API_BASE}/releases`, { headers: { Accept: 'application/vnd.github+json' } })
    if (!resp.ok) throw new Error(`GitHub API ${resp.status}`)
    const releases = await resp.json()
    if (!Array.isArray(releases) || releases.length === 0) {
      listEl.innerHTML = '<div class="release-empty">公開されているバージョンはまだありません。</div>'
      primaryMeta.textContent = 'まだリリースがありません'
      return
    }

    const published = releases.filter(r => !r.draft)
    const latest = published.find(r => !r.prerelease) || published[0]

    listEl.innerHTML = published.map(r => renderReleaseCard(r, r.id === latest.id)).join('')

    const zip = latest ? findZipAsset(latest) : null
    if (zip) {
      primaryLink.href = zip.browser_download_url
      primaryLabel.textContent = `ダウンロード (${latest.tag_name})`
      primaryMeta.textContent = `${zip.name} · ${formatBytes(zip.size)}`
    } else if (latest) {
      primaryLink.href = latest.html_url
      primaryLabel.textContent = `${latest.tag_name} のページを開く`
      primaryMeta.textContent = 'ビルドはまだアップロードされていません'
    }
  } catch (e) {
    listEl.hidden = true
    errorEl.hidden = false
    primaryMeta.textContent = '最新バージョン情報の取得に失敗しました'
  }
}

loadReleases()
