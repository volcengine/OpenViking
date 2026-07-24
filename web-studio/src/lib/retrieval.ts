import {
  getOvResult,
  normalizeOvClientError,
  postSearchFind,
  postSearchGlob,
  postSearchGrep,
  postSearchSearch,
} from '#/lib/ov-client'
import type { FindContextType, SearchResult } from '@ov-server/api/v1/search'

export type { FindContextType } from '@ov-server/api/v1/search'

export interface FindResultItem {
  uri: string
  context_type: FindContextType
  level: number
  score: number
  abstract: string
  overview?: string | null
  category: string
  match_reason: string
  relations: Array<{ uri: string; abstract: string }>
  result_kind?: 'semantic' | 'grep' | 'glob'
  line?: number
}

export interface FindQueryPlanItem {
  query: string
  context_type?: FindContextType | null
  intent?: string | null
  priority?: number | null
}

export interface FindQueryPlan {
  reasoning?: string | null
  queries: FindQueryPlanItem[]
}

export interface GroupedFindResult {
  memories: FindResultItem[]
  resources: FindResultItem[]
  skills: FindResultItem[]
  total: number
  query_plan?: FindQueryPlan | null
  provenance?: Array<Record<string, unknown>> | null
}

export interface VikingApiError {
  code: string
  message: string
  statusCode?: number
  details?: unknown
}

export interface FetchFindOptions {
  targetUri?: string
  limit?: number
  scoreThreshold?: number
  filter?: Record<string, unknown>
}

export interface FetchSearchOptions extends FetchFindOptions {
  sessionId?: string
}

export interface FetchGrepOptions {
  caseInsensitive?: boolean
  limit?: number
  uri: string
}

export interface FetchGlobOptions {
  limit?: number
  uri?: string
}

const FIND_CONTEXT_TYPES = ['resource', 'memory', 'skill'] as const

function toVikingApiError(error: unknown): VikingApiError {
  const normalized = normalizeOvClientError(error)
  return {
    code: normalized.code,
    details: normalized.details,
    message: normalized.message,
    statusCode: normalized.statusCode,
  }
}

function isFindContextType(value: unknown): value is FindContextType {
  return FIND_CONTEXT_TYPES.some((type) => type === value)
}

function normalizeFindItems(
  value: unknown,
  fallbackType: FindContextType,
): FindResultItem[] {
  if (!Array.isArray(value)) return []

  return value
    .filter(
      (item): item is Record<string, unknown> =>
        item !== null && typeof item === 'object' && !Array.isArray(item),
    )
    .map((item) => ({
      abstract: typeof item.abstract === 'string' ? item.abstract : '',
      category: typeof item.category === 'string' ? item.category : '',
      context_type: isFindContextType(item.context_type)
        ? item.context_type
        : fallbackType,
      level: typeof item.level === 'number' ? item.level : 2,
      match_reason:
        typeof item.match_reason === 'string' ? item.match_reason : '',
      overview: typeof item.overview === 'string' ? item.overview : null,
      relations: Array.isArray(item.relations)
        ? item.relations
            .filter(
              (relation): relation is Record<string, unknown> =>
                relation !== null &&
                typeof relation === 'object' &&
                !Array.isArray(relation),
            )
            .map((relation) => ({
              abstract:
                typeof relation.abstract === 'string' ? relation.abstract : '',
              uri: typeof relation.uri === 'string' ? relation.uri : '',
            }))
        : [],
      score: typeof item.score === 'number' ? item.score : 0,
      uri: typeof item.uri === 'string' ? item.uri : '',
    }))
}

function normalizeQueryPlan(value: unknown): FindQueryPlan | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'object' || Array.isArray(value)) return null

  const data = value as Record<string, unknown>
  const queries = Array.isArray(data.queries)
    ? data.queries
        .filter(
          (query): query is Record<string, unknown> =>
            query !== null &&
            typeof query === 'object' &&
            !Array.isArray(query),
        )
        .map<FindQueryPlanItem>((query) => ({
          context_type: isFindContextType(query.context_type)
            ? query.context_type
            : null,
          intent: typeof query.intent === 'string' ? query.intent : null,
          priority: typeof query.priority === 'number' ? query.priority : null,
          query: typeof query.query === 'string' ? query.query : '',
        }))
        .filter((query) => query.query.trim().length > 0)
    : []

  return {
    queries,
    reasoning: typeof data.reasoning === 'string' ? data.reasoning : null,
  }
}

function normalizeGroupedFindResult(
  result: SearchResult | unknown,
): GroupedFindResult {
  const data =
    result !== null && typeof result === 'object' && !Array.isArray(result)
      ? (result as Record<string, unknown>)
      : {}
  const memories = normalizeFindItems(data.memories, 'memory')
  const resources = normalizeFindItems(data.resources, 'resource')
  const skills = normalizeFindItems(data.skills, 'skill')
  const total =
    typeof data.total === 'number'
      ? data.total
      : memories.length + resources.length + skills.length

  return {
    memories,
    provenance: Array.isArray(data.provenance)
      ? data.provenance.filter(
          (item): item is Record<string, unknown> =>
            item !== null && typeof item === 'object' && !Array.isArray(item),
        )
      : null,
    query_plan: normalizeQueryPlan(data.query_plan),
    resources,
    skills,
    total,
  }
}

export async function fetchFind(
  query: string,
  options: FetchFindOptions = {},
): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult<SearchResult>(
      postSearchFind({
        body: {
          filter: options.filter,
          limit: options.limit ?? 10,
          query,
          score_threshold: options.scoreThreshold,
          target_uri: options.targetUri,
        },
      }),
    )

    return normalizeGroupedFindResult(result)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchSearch(
  query: string,
  options: FetchSearchOptions = {},
): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult<SearchResult>(
      postSearchSearch({
        body: {
          filter: options.filter,
          limit: options.limit ?? 10,
          query,
          score_threshold: options.scoreThreshold,
          session_id: options.sessionId,
          target_uri: options.targetUri,
        },
      }),
    )

    return normalizeGroupedFindResult(result)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

/**
 * Fetch all context types in one request.
 *
 * The server already groups resources, memories, and skills in its response,
 * so issuing one request per type only duplicates work and can exceed the
 * requested global limit.
 */
export function fetchFindAllTypes(
  query: string,
  options: Omit<FetchFindOptions, 'targetUri' | 'filter'> = {},
): Promise<GroupedFindResult> {
  return fetchFind(query, options)
}

function emptyPatternResult(resources: FindResultItem[]): GroupedFindResult {
  return {
    memories: [],
    resources,
    skills: [],
    total: resources.length,
  }
}

function patternResultItem(
  uri: string,
  kind: 'grep' | 'glob',
  options: { content?: string; line?: number } = {},
): FindResultItem {
  return {
    abstract: options.content ?? '',
    category: '',
    context_type: 'resource',
    level: 2,
    line: options.line,
    match_reason: '',
    relations: [],
    result_kind: kind,
    score: 0,
    uri,
  }
}

export async function fetchGrep(
  pattern: string,
  options: FetchGrepOptions,
): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult<unknown>(
      postSearchGrep({
        body: {
          case_insensitive: options.caseInsensitive,
          node_limit: options.limit ?? 10,
          pattern,
          uri: options.uri,
        },
      }),
    )
    const matches =
      result && typeof result === 'object' && 'matches' in result
        ? (result as { matches?: unknown }).matches
        : undefined
    const resources = Array.isArray(matches)
      ? matches
          .filter(
            (match): match is Record<string, unknown> =>
              match !== null &&
              typeof match === 'object' &&
              !Array.isArray(match) &&
              typeof match.uri === 'string',
          )
          .map((match) =>
            patternResultItem(match.uri as string, 'grep', {
              content:
                typeof match.content === 'string' ? match.content : undefined,
              line: typeof match.line === 'number' ? match.line : undefined,
            }),
          )
      : []
    return emptyPatternResult(resources)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchGlob(
  pattern: string,
  options: FetchGlobOptions = {},
): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult<unknown>(
      postSearchGlob({
        body: {
          node_limit: options.limit ?? 10,
          pattern,
          uri: options.uri ?? 'viking://',
        },
      }),
    )
    const matches =
      result && typeof result === 'object' && 'matches' in result
        ? (result as { matches?: unknown }).matches
        : undefined
    const resources = Array.isArray(matches)
      ? matches
          .filter((uri): uri is string => typeof uri === 'string')
          .map((uri) => patternResultItem(uri, 'glob'))
      : []
    return emptyPatternResult(resources)
  } catch (error) {
    throw toVikingApiError(error)
  }
}
