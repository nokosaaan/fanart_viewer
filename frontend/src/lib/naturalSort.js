/**
 * Natural sort comparison for strings containing numbers (e.g. "05.jpg", "15.webp", "2.jpg", "10.jpg").
 * Uses Intl.Collator with numeric: true for accurate natural ordering.
 */
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function naturalCompare(a, b) {
  return collator.compare(a || '', b || '')
}

/**
 * Sorts an array of File objects or objects with a `name` / `filename` property naturally.
 * Returns a new sorted array.
 */
export function sortFilesNaturally(files, reverse = false) {
  const sorted = [...files].sort((a, b) => {
    const nameA = a.name || a.filename || ''
    const nameB = b.name || b.filename || ''
    return collator.compare(nameA, nameB)
  })
  return reverse ? sorted.reverse() : sorted
}

