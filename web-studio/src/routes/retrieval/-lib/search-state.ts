import {
  DEFAULT_RESULT_COUNT,
  DEFAULT_RETRIEVAL_MODE,
  DEFAULT_RETRIEVAL_SCOPE,
  LAST_RETRIEVAL_SEARCH_KEY,
  RESULT_COUNT_OPTIONS,
  RETRIEVAL_MODES,
  RETRIEVAL_SCOPES,
} from '../-constants/retrieval'
import type {
  ResultCountOption,
  RetrievalMode,
  RetrievalScope,
  RetrievalSearch,
} from '../-types/retrieval'

export function isRetrievalMode(value: unknown): value is RetrievalMode {
  return (
    typeof value === 'string' &&
    (RETRIEVAL_MODES as readonly string[]).includes(value)
  )
}

export function isRetrievalScope(value: unknown): value is RetrievalScope {
  return (
    typeof value === 'string' &&
    (RETRIEVAL_SCOPES as readonly string[]).includes(value)
  )
}

export function parseResultCount(value: unknown): number | undefined {
  const numeric =
    typeof value === 'number'
      ? value
      : typeof value === 'string'
        ? Number(value)
        : NaN
  return RESULT_COUNT_OPTIONS.includes(numeric as ResultCountOption)
    ? numeric
    : undefined
}

export function validateRetrievalSearch(
  search: Record<string, unknown>,
): RetrievalSearch {
  const q = typeof search.q === 'string' ? search.q.trim() : undefined
  const count = parseResultCount(search.count)
  const path = typeof search.path === 'string' ? search.path.trim() : undefined
  const session =
    typeof search.session === 'string' ? search.session.trim() : undefined

  return {
    ...(q && { q }),
    ...(isRetrievalMode(search.mode) && { mode: search.mode }),
    ...(count && { count }),
    ...(isRetrievalScope(search.scope) && { scope: search.scope }),
    ...(path && { path }),
    ...(session && { session }),
  }
}

export function hasRetrievalSearch(search: RetrievalSearch): boolean {
  return Boolean(
    search.q ||
    search.mode ||
    search.count ||
    search.scope ||
    search.path ||
    search.session,
  )
}

export function readLastRetrievalSearch(): RetrievalSearch | undefined {
  if (typeof window === 'undefined') {
    return undefined
  }

  try {
    const raw = window.sessionStorage.getItem(LAST_RETRIEVAL_SEARCH_KEY)
    if (!raw) {
      return undefined
    }

    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return undefined
    }

    const search = validateRetrievalSearch(parsed as Record<string, unknown>)
    return search.q ? search : undefined
  } catch {
    return undefined
  }
}

export function writeLastRetrievalSearch(search: RetrievalSearch) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.sessionStorage.setItem(
      LAST_RETRIEVAL_SEARCH_KEY,
      JSON.stringify(search),
    )
  } catch {
    // Ignore storage failures in restricted environments.
  }
}

export function buildSubmittedSearch(params: {
  q: string
  mode: RetrievalMode
  count: number
  scope: RetrievalScope
  path: string
  session: string
}): RetrievalSearch {
  const q = params.q.trim()
  const path = params.path.trim()
  const session = params.session.trim()

  return {
    q,
    ...(params.mode !== DEFAULT_RETRIEVAL_MODE && { mode: params.mode }),
    ...(params.count !== DEFAULT_RESULT_COUNT && { count: params.count }),
    ...(params.scope !== DEFAULT_RETRIEVAL_SCOPE && { scope: params.scope }),
    ...(params.scope === 'custom' && path && { path }),
    ...(params.mode === 'search' && session && { session }),
  }
}
