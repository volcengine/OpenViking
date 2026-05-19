import {
  fileNameFromUri,
  normalizeDirUri,
  normalizeFileUri,
  parentUri as getParentUri,
} from '#/lib/viking-uri'
import type { GroupedFindResult, FindResultItem } from '#/lib/retrieval'

import type { FlatRetrievalItem } from '../-types/retrieval'

export function flattenResults(data: GroupedFindResult): FlatRetrievalItem[] {
  const items: FlatRetrievalItem[] = []
  let idx = 0
  for (const r of data.resources)
    items.push({ type: 'resource', item: r, flatIndex: idx++ })
  for (const m of data.memories)
    items.push({ type: 'memory', item: m, flatIndex: idx++ })
  for (const s of data.skills)
    items.push({ type: 'skill', item: s, flatIndex: idx++ })
  return items
}

export function displayName(uri: string): { name: string; parent: string } {
  const name = fileNameFromUri(uri)
  const dir = getParentUri(uri)
  const segments = dir.replace(/\/$/, '').split('/').filter(Boolean)
  const parent = segments.length > 1 ? segments.slice(-1)[0] : dir
  return { name, parent }
}

export function isDirectoryResult(item: FindResultItem): boolean {
  return item.uri.endsWith('/') || item.level < 2
}

export function resourceSearchForResult(item: FindResultItem): {
  uri: string
  file?: string
} {
  const uri = item.uri.trim()
  if (!uri) {
    return { uri: 'viking://' }
  }

  if (isDirectoryResult(item)) {
    return { uri: normalizeDirUri(uri) }
  }

  const file = normalizeFileUri(uri)
  return { uri: getParentUri(file), file }
}
