import {
  fileNameFromUri,
  normalizeDirUri,
  normalizeFileUri,
  parentUri,
} from '#/shared/lib/viking-fs'
import type { VikingFsEntry } from '#/shared/types/viking-fs'

import { cleanVikingUri } from '#/shared/lib/viking-uri'

import {
  FILESYSTEM_AGENT_SESSIONS_STORAGE_KEY,
  FILESYSTEM_EXPANDED_URIS_STORAGE_KEY,
  ROOT_URI,
} from '#/features/workbench/filesystem/lib/constants'

export { cleanVikingUri }

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object') {
    const data = error as {
      code?: unknown
      message?: unknown
      statusCode?: unknown
    }
    const code = typeof data.code === 'string' ? data.code : ''
    const message = typeof data.message === 'string' ? data.message : ''
    const status =
      typeof data.statusCode === 'number' ? `HTTP ${data.statusCode}` : ''
    const readable = [status, code, message].filter(Boolean).join(' · ')
    if (readable) return readable

    try {
      return JSON.stringify(error)
    } catch {
      return String(error)
    }
  }
  return String(error)
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

export function readStoredJsonArray<T>(
  key: string,
  validator: (value: unknown) => T | undefined,
  limit?: number,
  fromEnd = false,
): T[] {
  if (typeof window === 'undefined') return []

  try {
    const raw = window.localStorage.getItem(key)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return []
    const values = parsed.flatMap((item) => {
      const value = validator(item)
      return value === undefined ? [] : [value]
    })
    if (limit === undefined) return values
    return fromEnd ? values.slice(-limit) : values.slice(0, limit)
  } catch {
    return []
  }
}

export function writeStoredJson(key: string, value: unknown): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Ignore storage failures in restricted browser contexts.
  }
}

export function createIdentityStorageKey(
  key: string,
  identityScopeKey: string,
): string {
  return `${key}.${encodeURIComponent(identityScopeKey)}`
}

export function readFilesystemAgentSessionIds(
  identityScopeKey: string,
): string[] {
  return readStoredJsonArray(
    createIdentityStorageKey(
      FILESYSTEM_AGENT_SESSIONS_STORAGE_KEY,
      identityScopeKey,
    ),
    (sessionId) =>
      typeof sessionId === 'string' && sessionId.length > 0
        ? sessionId
        : undefined,
  )
}

export function registerFilesystemAgentSessionId(
  sessionId: string,
  identityScopeKey: string,
): string[] {
  const trimmed = sessionId.trim()
  if (typeof window === 'undefined') return trimmed ? [trimmed] : []
  if (!trimmed) return readFilesystemAgentSessionIds(identityScopeKey)

  const next = [
    trimmed,
    ...readFilesystemAgentSessionIds(identityScopeKey).filter(
      (item) => item !== trimmed,
    ),
  ].slice(0, 50)

  writeStoredJson(
    createIdentityStorageKey(
      FILESYSTEM_AGENT_SESSIONS_STORAGE_KEY,
      identityScopeKey,
    ),
    next,
  )

  return next
}

export function readFilesystemExpandedUris(identityScopeKey: string): string[] {
  return readStoredJsonArray(
    createIdentityStorageKey(
      FILESYSTEM_EXPANDED_URIS_STORAGE_KEY,
      identityScopeKey,
    ),
    (uri) =>
      typeof uri === 'string' && uri.startsWith(ROOT_URI)
        ? normalizeDirUri(uri)
        : undefined,
    500,
  )
}

export function writeFilesystemExpandedUris(
  identityScopeKey: string,
  uris: Iterable<string>,
): void {
  writeStoredJson(
    createIdentityStorageKey(
      FILESYSTEM_EXPANDED_URIS_STORAGE_KEY,
      identityScopeKey,
    ),
    [...uris],
  )
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

export function isDirectoryLevelFile(uri: string): boolean {
  const name = fileNameFromUri(uri)
  return name === '_abstract.md' || name === '_overview.md'
}

export function normalizeFilesystemResourceUri(uri: string): string {
  const normalized = uri.endsWith('/')
    ? normalizeDirUri(uri)
    : normalizeFileUri(uri)
  if (!normalized.endsWith('/') && isDirectoryLevelFile(normalized)) {
    return normalizeDirUri(parentUri(normalized))
  }
  return normalized
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
    // Directories first.
    if (left.isDir !== right.isDir) return left.isDir ? -1 : 1
    // Sort by modTime descending (newest first). Fall back to name when
    // timestamps are missing or equal.
    const leftTime = left.modTimestamp ?? 0
    const rightTime = right.modTimestamp ?? 0
    if (leftTime !== rightTime) return rightTime - leftTime
    return left.name.localeCompare(right.name)
  })
}
