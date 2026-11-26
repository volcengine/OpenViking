import { normalizeVikingUri } from './uri'

const STORAGE_KEY = 'ov-arranger-path-history'
const HISTORY_LIMIT = 20

function storageKey(identityScopeKey: string): string {
  return `${STORAGE_KEY}.${encodeURIComponent(identityScopeKey)}`
}

function normalizePaths(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  const paths = new Set<string>()
  for (const value of values) {
    if (typeof value !== 'string') continue
    try {
      paths.add(normalizeVikingUri(value))
    } catch {
      // Ignore invalid entries in browser storage.
    }
    if (paths.size === HISTORY_LIMIT) break
  }
  return [...paths]
}

export function readPathHistory(identityScopeKey: string): string[] {
  try {
    const raw = localStorage.getItem(storageKey(identityScopeKey))
    return raw ? normalizePaths(JSON.parse(raw)) : []
  } catch {
    return []
  }
}

export function rememberPath(
  identityScopeKey: string,
  uri: string,
  previous: string[],
): string[] {
  const paths = normalizePaths([normalizeVikingUri(uri), ...previous])
  try {
    localStorage.setItem(storageKey(identityScopeKey), JSON.stringify(paths))
  } catch {
    // Keep history usable in memory when browser storage is unavailable.
  }
  return paths
}
