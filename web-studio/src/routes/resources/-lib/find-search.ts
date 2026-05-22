import type { VikingFsEntry } from '../-types/viking-fm'
import { normalizeDirUri } from './normalize'

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
    return {
      mode: 'path',
      query: normalizeVikingPathSearchQuery(trimmed),
      rootUri: VIKING_URI_PREFIX,
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
    return entry.uri.startsWith(spec.query)
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
