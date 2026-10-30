import type { FindResultItem, GroupedFindResult } from '#/shared/api/retrieval'
import {
  fileNameFromUri,
  parentUri as getParentUri,
  normalizeDirUri,
  normalizeFileUri,
} from '#/shared/lib/viking-uri'

import type { FlatRetrievalItem } from '#/features/workbench/retrieval/types/retrieval'

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

const MEMORY_TYPE_PATTERN = /\/memories\/([^/]+)(?:\/|$)/i

export function memoryTypeFromUri(uri: string): string | undefined {
  const match = MEMORY_TYPE_PATTERN.exec(uri)
  const type = match?.[1]?.trim().replace(/\.(?:md|json|jsonl)$/i, '')
  return type ? type.toUpperCase() : undefined
}

export function isDirectoryResult(item: FindResultItem): boolean {
  const uri = item.uri.trim()
  // L0/L1 can identify a materialized summary file rather than its directory.
  const name = fileNameFromUri(uri)
  if (name === '.abstract.md' || name === '.overview.md') return false
  return uri.endsWith('/') || item.level < 2
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
