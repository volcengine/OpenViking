import {
  DEFAULT_RESULT_COUNT,
  DEFAULT_RECALL_MAX_CHARS,
  DEFAULT_RECALL_QUOTAS,
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
  RecallPeerScope,
  RetrievalScope,
  RetrievalSearch,
  RetrievalTimeField,
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
  const ignoreCase =
    search.ignoreCase === true || search.ignoreCase === 'true' || undefined
  const provenance =
    search.provenance === true || search.provenance === 'true' || undefined
  const render =
    search.render === false || search.render === 'false' ? false : undefined
  const minScore = parseFiniteNumber(search.minScore)
  const maxChars = parsePositiveInteger(search.maxChars)
  const timeField = isTimeField(search.timeField) ? search.timeField : undefined
  const peerScope = isPeerScope(search.peerScope) ? search.peerScope : undefined

  return {
    ...(q && { q }),
    ...(isRetrievalMode(search.mode) && { mode: search.mode }),
    ...(count && { count }),
    ...(isRetrievalScope(search.scope) && { scope: search.scope }),
    ...(path && { path }),
    ...(session && { session }),
    ...(ignoreCase && { ignoreCase }),
    ...(typeof search.types === 'string' &&
      search.types.trim() && { types: search.types.trim() }),
    ...(typeof search.tags === 'string' &&
      search.tags.trim() && { tags: search.tags.trim() }),
    ...(minScore !== undefined && { minScore }),
    ...(typeof search.levels === 'string' &&
      search.levels.trim() && { levels: search.levels.trim() }),
    ...(typeof search.since === 'string' &&
      search.since.trim() && { since: search.since.trim() }),
    ...(typeof search.until === 'string' &&
      search.until.trim() && { until: search.until.trim() }),
    ...(timeField && { timeField }),
    ...(provenance && { provenance }),
    ...(typeof search.recallQuotas === 'string' &&
      search.recallQuotas.trim() && {
        recallQuotas: search.recallQuotas.trim(),
      }),
    ...(maxChars !== undefined && { maxChars }),
    ...(peerScope && { peerScope }),
    ...(render === false && { render }),
  }
}

export function hasRetrievalSearch(search: RetrievalSearch): boolean {
  return Boolean(
    search.q ||
    search.mode ||
    search.count ||
    search.scope ||
    search.path ||
    search.session ||
    search.ignoreCase ||
    search.types ||
    search.tags ||
    search.minScore !== undefined ||
    search.levels ||
    search.since ||
    search.until ||
    search.timeField ||
    search.provenance ||
    search.recallQuotas ||
    search.maxChars ||
    search.peerScope ||
    search.render === false,
  )
}

function parseFiniteNumber(value: unknown): number | undefined {
  const numeric =
    typeof value === 'number'
      ? value
      : typeof value === 'string'
        ? Number(value)
        : NaN
  return Number.isFinite(numeric) ? numeric : undefined
}

function parsePositiveInteger(value: unknown): number | undefined {
  const numeric = parseFiniteNumber(value)
  return numeric !== undefined && numeric > 0 ? Math.floor(numeric) : undefined
}

function isTimeField(value: unknown): value is RetrievalTimeField {
  return value === 'created_at' || value === 'updated_at'
}

function isPeerScope(value: unknown): value is RecallPeerScope {
  return value === 'actor' || value === 'all'
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
  ignoreCase: boolean
  types: string[]
  tags: string[]
  scoreThreshold?: number
  levels: number[]
  since: string
  until: string
  timeField: RetrievalTimeField
  includeProvenance: boolean
  recallQuotas: Record<string, number>
  recallMaxChars: number
  recallPeerScope: RecallPeerScope
  recallRender: boolean
}): RetrievalSearch {
  const q = params.q.trim()
  const path = params.path.trim()
  const session = params.session.trim()
  const types = params.types.join(',')
  const tags = params.tags.join(',')
  const levels = params.levels.join(',')
  const since = params.since.trim()
  const until = params.until.trim()
  const recallQuotas = Object.entries(params.recallQuotas)
    .map(([key, value]) => `${key}:${value}`)
    .join(',')
  const defaultRecallQuotas = Object.entries(DEFAULT_RECALL_QUOTAS)
    .map(([key, value]) => `${key}:${value}`)
    .join(',')

  return {
    q,
    ...(params.mode !== DEFAULT_RETRIEVAL_MODE && { mode: params.mode }),
    ...(params.count !== DEFAULT_RESULT_COUNT && { count: params.count }),
    ...(params.scope !== DEFAULT_RETRIEVAL_SCOPE && { scope: params.scope }),
    ...(params.scope === 'custom' && path && { path }),
    ...(params.mode === 'search' && session && { session }),
    ...(params.mode === 'grep' && params.ignoreCase && { ignoreCase: true }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      types && { types }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      tags && { tags }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      params.scoreThreshold !== undefined && {
        minScore: params.scoreThreshold,
      }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      levels && { levels }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      since && { since }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      until && { until }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      (since || until) &&
      params.timeField !== 'updated_at' && { timeField: params.timeField }),
    ...((params.mode === 'find' || params.mode === 'search') &&
      params.includeProvenance && { provenance: true }),
    ...(params.mode === 'recall' &&
      recallQuotas !== defaultRecallQuotas && { recallQuotas }),
    ...(params.mode === 'recall' &&
      params.recallMaxChars !== DEFAULT_RECALL_MAX_CHARS && {
        maxChars: params.recallMaxChars,
      }),
    ...(params.mode === 'recall' &&
      params.scoreThreshold !== undefined && {
        minScore: params.scoreThreshold,
      }),
    ...(params.mode === 'recall' &&
      params.recallPeerScope !== 'all' && {
        peerScope: params.recallPeerScope,
      }),
    ...(params.mode === 'recall' && !params.recallRender && { render: false }),
  }
}

export function parseCsv(value?: string): string[] {
  if (!value) return []
  return Array.from(
    new Set(
      value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  )
}

export function parseLevels(value?: string): number[] {
  return parseCsv(value)
    .map(Number)
    .filter((level) => level === 0 || level === 1 || level === 2)
}

export function parseRecallQuotas(
  value?: string,
): Record<keyof typeof DEFAULT_RECALL_QUOTAS, number> {
  const quotas = { ...DEFAULT_RECALL_QUOTAS }
  for (const part of parseCsv(value)) {
    const [key, rawValue] = part.split(':')
    if (!(key in quotas)) continue
    const numeric = Number(rawValue)
    if (Number.isFinite(numeric) && numeric >= 0) {
      quotas[key as keyof typeof quotas] = Math.floor(numeric)
    }
  }
  return quotas
}
