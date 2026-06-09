import type { VikingFsEntry } from '../-types/viking-fm'
import { normalizeDirUri, normalizeFileUri, parentUri } from './normalize'

const VIKING_URI_PREFIX = 'viking://'

export type ResourceSearchSpec =
  | {
      mode: 'name'
      query: string
      rootUri: string
    }
  | {
      mode: 'path'
      query: string
      rootUri: string
    }

function rootUriForPathSearch(query: string): string {
  if (query === VIKING_URI_PREFIX) {
    return VIKING_URI_PREFIX
  }
  if (query.endsWith('/')) {
    return normalizeDirUri(query)
  }
  return parentUri(normalizeFileUri(query))
}

export function isVikingPathSearchQuery(query: string): boolean {
  return query.trimStart().toLowerCase().startsWith(VIKING_URI_PREFIX)
}

export function normalizeVikingPathSearchQuery(query: string): string {
  const trimmed = query.trim()
  if (!isVikingPathSearchQuery(trimmed)) {
    return ''
  }

  const path = trimmed.slice(VIKING_URI_PREFIX.length)
  const hasTrailingSlash = path.endsWith('/')
  const normalizedPath = path.split('/').filter(Boolean).join('/')

  if (!normalizedPath) {
    return VIKING_URI_PREFIX
  }

  return `${VIKING_URI_PREFIX}${normalizedPath}${hasTrailingSlash ? '/' : ''}`
}

export function getResourceSearchSpec(
  query: string,
  scopeUri: string,
): ResourceSearchSpec | null {
  const trimmed = query.trim()
  if (!trimmed) {
    return null
  }

  if (isVikingPathSearchQuery(trimmed)) {
    const normalizedQuery = normalizeVikingPathSearchQuery(trimmed)
    return {
      mode: 'path',
      query: normalizedQuery,
      rootUri: rootUriForPathSearch(normalizedQuery),
    }
  }

  return {
    mode: 'name',
    query: trimmed.toLowerCase(),
    rootUri: normalizeDirUri(scopeUri),
  }
}

export function matchesResourceSearch(
  entry: VikingFsEntry,
  spec: ResourceSearchSpec,
): boolean {
  if (!entry.uri.startsWith(spec.rootUri)) {
    return false
  }

  if (spec.mode === 'path') {
    const dirPrefix = normalizeDirUri(spec.query)
    return (
      entry.uri === spec.query ||
      entry.uri === dirPrefix ||
      entry.uri.startsWith(dirPrefix)
    )
  }

  return entry.name.toLowerCase().includes(spec.query)
}

export function filterResourceSearchEntries(
  entries: Array<VikingFsEntry>,
  spec: ResourceSearchSpec | null,
): Array<VikingFsEntry> {
  if (!spec) {
    return []
  }

  return entries.filter((entry) => matchesResourceSearch(entry, spec))
}
