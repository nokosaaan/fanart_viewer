// Which CharacterGroups belong to the title(s) an item has -- the scope the
// character picker and the region annotator offer candidates from. Mirrors
// the backend's item/character_groups.py (groups_for_title) so what is filed
// automatically at save time is also what shows up when picking.
//
// A group belongs to a title when the title is in its `titles`; a legacy group
// that was never linked (empty `titles`) belongs to the title it is NAMED
// after. The matching groups' ANCESTORS are included too: a character that
// belongs to a whole franchise sits on the broad parent group, but appears in
// every sub-title's artwork.
export function groupsForTitles(groups, titles) {
  const wanted = (titles || []).filter(Boolean)
  if (wanted.length === 0) return []
  const byId = new Map(groups.map(g => [g.id, g]))
  const picked = new Map()
  for (const title of wanted) {
    let claimants = groups.filter(g => (g.titles || []).includes(title))
    if (claimants.length === 0) {
      claimants = groups.filter(g => (g.titles || []).length === 0 && g.name === title)
    }
    for (const g of claimants) {
      let node = g
      const seen = new Set()
      while (node && !seen.has(node.id)) {
        seen.add(node.id)
        picked.set(node.id, node)
        node = node.parent != null ? byId.get(node.parent) : null
      }
    }
  }
  return groups.filter(g => picked.has(g.id))
}
