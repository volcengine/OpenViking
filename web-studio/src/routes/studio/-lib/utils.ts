import {
  fileNameFromUri,
  normalizeDirUri,
  normalizeFileUri,
  parentUri,
} from '#/routes/resources/-lib/normalize'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'
import type { FindResultItem, GroupedFindResult } from '#/lib/retrieval'

import { cleanVikingUri } from '#/lib/viking-uri'

import { ROOT_URI, STUDIO_AGENT_SESSIONS_STORAGE_KEY } from './constants'
import type { ResourceRef } from './types'

export { cleanVikingUri }

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

export function readStoredNumber(
  key: string,
  fallback: number,
  min: number,
  max: number,
): number {
  if (typeof window === 'undefined') return fallback

  const raw = window.localStorage.getItem(key)
  if (raw === null) return fallback

  const value = Number(raw)
  return Number.isFinite(value) ? clampNumber(value, min, max) : fallback
}

export function readStudioAgentSessionIds(): string[] {
  if (typeof window === 'undefined') return []

  try {
    const raw = window.localStorage.getItem(STUDIO_AGENT_SESSIONS_STORAGE_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : []
    if (!Array.isArray(parsed)) return []

    return parsed.filter(
      (sessionId): sessionId is string =>
        typeof sessionId === 'string' && sessionId.length > 0,
    )
  } catch {
    return []
  }
}

export function registerStudioAgentSessionId(sessionId: string): string[] {
  const trimmed = sessionId.trim()
  if (typeof window === 'undefined') return trimmed ? [trimmed] : []
  if (!trimmed) return readStudioAgentSessionIds()

  const next = [
    trimmed,
    ...readStudioAgentSessionIds().filter((item) => item !== trimmed),
  ].slice(0, 50)

  try {
    window.localStorage.setItem(
      STUDIO_AGENT_SESSIONS_STORAGE_KEY,
      JSON.stringify(next),
    )
  } catch {
    // Ignore storage failures in restricted browser contexts.
  }

  return next
}

export function createEntryFromUri(uri: string, isDir: boolean): VikingFsEntry {
  const normalized = isDir ? normalizeDirUri(uri) : normalizeFileUri(uri)
  return {
    abstract: '',
    isDir,
    modTime: '',
    modTimestamp: null,
    name: fileNameFromUri(normalized) || normalized,
    overview: '',
    size: '',
    sizeBytes: null,
    uri: normalized,
  }
}

export function getAncestorUris(uri: string): string[] {
  const normalized = normalizeDirUri(uri)
  if (normalized === ROOT_URI) return [ROOT_URI]

  const body = normalized.slice(ROOT_URI.length, -1)
  const parts = body.split('/').filter(Boolean)
  const ancestors = [ROOT_URI]
  let running = ROOT_URI

  for (const part of parts) {
    running = `${running}${part}/`
    ancestors.push(running)
  }

  return ancestors
}

export function mergeExpanded(
  current: Set<string>,
  uris: string[],
): Set<string> {
  const next = new Set(current)
  for (const uri of uris) next.add(uri)
  return next
}

export function buildBreadcrumbs(
  uri: string,
  isFile: boolean,
): Array<{ label: string; uri: string }> {
  const normalized = isFile ? normalizeFileUri(uri) : normalizeDirUri(uri)
  const dirUri = isFile ? normalizeDirUri(parentUri(normalized)) : normalized
  const body = dirUri.slice(ROOT_URI.length).replace(/\/$/, '')
  const parts = body ? body.split('/').filter(Boolean) : []
  const crumbs: Array<{ label: string; uri: string }> = [
    { label: ROOT_URI, uri: ROOT_URI },
  ]
  let running = ROOT_URI

  for (const part of parts) {
    running = `${running}${part}/`
    crumbs.push({ label: part, uri: running })
  }

  if (isFile) {
    crumbs.push({
      label: fileNameFromUri(normalized) || normalized,
      uri: normalized,
    })
  }

  return crumbs
}

export function isDirectoryLevelFile(uri: string): boolean {
  const name = fileNameFromUri(uri)
  return name === '_abstract.md' || name === '_overview.md'
}

export function normalizeStudioResourceUri(uri: string): string {
  const normalized = uri.endsWith('/')
    ? normalizeDirUri(uri)
    : normalizeFileUri(uri)
  if (!normalized.endsWith('/') && isDirectoryLevelFile(normalized)) {
    return normalizeDirUri(parentUri(normalized))
  }
  return normalized
}

export function entryToRef(entry: VikingFsEntry): ResourceRef {
  return {
    label: entry.name || fileNameFromUri(entry.uri),
    meta: entry.isDir ? 'dir' : entry.size || undefined,
    uri: entry.uri,
  }
}

export function visibleContextEntries(
  entries: VikingFsEntry[],
): VikingFsEntry[] {
  return entries.filter(
    (entry) => entry.isDir || !isDirectoryLevelFile(entry.uri),
  )
}

export function sortTreeEntries(entries: VikingFsEntry[]): VikingFsEntry[] {
  return [...entries].sort((left, right) => {
    if (left.isDir !== right.isDir) return left.isDir ? -1 : 1
    return left.name.localeCompare(right.name)
  })
}

export function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  message: string,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error(message))
    }, timeoutMs)

    promise.then(
      (value) => {
        window.clearTimeout(timeoutId)
        resolve(value)
      },
      (error) => {
        window.clearTimeout(timeoutId)
        reject(error)
      },
    )
  })
}

export function formatScore(score: number): string {
  if (!Number.isFinite(score)) return 'score -'
  return `score ${score.toFixed(2)}`
}

type FindGroup = 'memories' | 'resources' | 'skills'

/**
 * Flatten a grouped search result (resources + memories + skills) into a single
 * ordered ref list, tagging each ref with its (localized) type, level and score
 * so nothing is dropped from the terminal output.
 */
export function searchResultToRefs(
  result: GroupedFindResult,
  groupLabels: Record<FindGroup, string>,
): ResourceRef[] {
  const groups: Array<[FindGroup, FindResultItem[]]> = [
    ['resources', result.resources],
    ['memories', result.memories],
    ['skills', result.skills],
  ]

  return groups.flatMap(([group, items]) =>
    items.map((item) => ({
      label: fileNameFromUri(item.uri) || item.uri,
      meta: `${groupLabels[group]} · L${item.level} · ${formatScore(item.score)}`,
      uri: item.uri,
    })),
  )
}
