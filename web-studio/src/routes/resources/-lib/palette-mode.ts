import { normalizeDirUri } from './normalize'

// Single source of truth for interpreting the palette query string.
// Nothing else may structurally parse `query` (no startsWith('/'), === '//',
// slice, split, etc.) — route every read through parsePaletteMode and every
// write through buildDirBrowseQuery. This keeps the stringly state machine
// from spreading back into the components.

export const PALETTE_ROOT_URI = 'viking://'

export const PALETTE_COMMANDS = {
  dir: '/',
  resetGlobal: '//',
} as const

export type PaletteMode =
  | { kind: 'idle' }
  | { kind: 'search'; query: string }
  | { kind: 'dirBrowse'; uri: string; filter: string }

export function parsePaletteMode(query: string, scopeUri: string): PaletteMode {
  const trimmed = query.trim()
  if (trimmed.length === 0) {
    return { kind: 'idle' }
  }
  if (trimmed.startsWith(PALETTE_COMMANDS.dir)) {
    return { kind: 'dirBrowse', ...parseDirBrowse(trimmed, scopeUri) }
  }
  return { kind: 'search', query: trimmed }
}

function parseDirBrowse(
  trimmed: string,
  scopeUri: string,
): { uri: string; filter: string } {
  const raw = trimmed.slice(PALETTE_COMMANDS.dir.length)
  const lastSlash = raw.lastIndexOf('/')
  // No inner slash ("/", "/abc"): browse the current scope, optional filter.
  if (lastSlash === -1) {
    return { uri: normalizeDirUri(scopeUri), filter: raw }
  }
  // Inner/trailing slash: the path is root-absolute so it round-trips with
  // buildDirBrowseQuery (empty path -> root, which makes root representable).
  const pathPart = raw.slice(0, lastSlash)
  const filter = raw.slice(lastSlash + 1)
  const parts = pathPart
    .split('/')
    .map((p) => p.trim())
    .filter(Boolean)
  const uri =
    parts.length > 0
      ? normalizeDirUri(`${PALETTE_ROOT_URI}${parts.join('/')}`)
      : PALETTE_ROOT_URI
  return { uri, filter }
}

export function isResetGlobalCommand(query: string): boolean {
  return query.trim() === PALETTE_COMMANDS.resetGlobal
}

// Inverse of parseDirBrowse: the query string that navigates to `uri`.
// Confines the `viking://` literal to this module; when the URI domain module
// lands (deferred), only this file changes.
export function buildDirBrowseQuery(uri: string): string {
  const normalized = normalizeDirUri(uri)
  if (normalized === PALETTE_ROOT_URI) {
    return PALETTE_COMMANDS.resetGlobal
  }
  const path = normalized.slice(PALETTE_ROOT_URI.length)
  return `${PALETTE_COMMANDS.dir}${path}`
}
