import type { GroupedFindResult } from '#/lib/retrieval'
import { parseOkfSidecarMarkdown } from '#/lib/okf-markdown'
import { retrievalResultNameFromUri } from '#/lib/viking-uri'

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

// A pattern without `/` matches at any depth (implicit `**/` prefix); one that
// contains `/` is treated as already anchored — matches gitignore / VS Code
// files-to-include / fd conventions.
export function normalizeGlobPattern(pattern: string): string {
  const trimmed = pattern.trim()
  return trimmed.includes('/') ? trimmed : `**/${trimmed}`
}

export function retrievalItemsToEntries(
  result: GroupedFindResult | undefined,
): Array<VikingFsEntry> {
  if (!result) return []
  const items = [...result.resources, ...result.memories, ...result.skills]
  return items.map((item) => ({
    uri: item.uri,
    name:
      retrievalResultNameFromUri(item.uri) +
      (item.line === undefined ? '' : `:${item.line}`),
    isDir: item.uri.endsWith('/'),
    abstract: item.abstract,
    // ponytail: reuse the `size` slot to surface the semantic score.
    size:
      item.result_kind === 'grep' || item.result_kind === 'glob'
        ? ''
        : item.score.toFixed(2),
    sizeBytes: null,
    modTime: '',
    modTimestamp: null,
  }))
}

export function resourceEntryAbstractForDisplay(
  entry: Pick<VikingFsEntry, 'abstract' | 'isDir' | 'uri'>,
): string {
  const content = entry.abstract.trim()
  if (!content || !entry.isDir) return content

  const looksLikeGeneratedSidecar =
    /^---\r?\ndirectory:\s*viking:\/\//.test(content) &&
    /\r?\ngenerated_by:/.test(content)
  if (!looksLikeGeneratedSidecar) return content

  const document = parseOkfSidecarMarkdown(
    `${normalizeDirUri(entry.uri)}.abstract.md`,
    content,
  )
  if (document) return document.body.trim()

  // Directory abstracts returned in list/search payloads may be clipped before
  // the closing `---`. Do not surface that partial YAML as a human summary.
  return ''
}
