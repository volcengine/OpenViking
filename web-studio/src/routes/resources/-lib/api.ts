import { getContentRead, getFsLs, getFsStat, getFsTree, getOvResult, normalizeOvClientError, postContentWrite, postSearchFind, postSearchSearch } from '#/lib/ov-client'

import { fileNameFromUri, formatModTime, normalizeDirUri, normalizeFsEntries, normalizeReadContent } from './normalize'
import type {
  FindContextType,
  FindQueryPlan,
  FindQueryPlanItem,
  FindResultItem,
  GroupedFindResult,
  VikingApiError,
  VikingFsEntry,
  VikingListQueryOptions,
  VikingListResult,
  VikingReadQueryOptions,
  VikingReadResult,
  VikingTreeQueryOptions,
  VikingTreeResult,
} from '../-types/viking-fm'

function toVikingApiError(error: unknown): VikingApiError {
  const normalized = normalizeOvClientError(error)
  return {
    code: normalized.code,
    message: normalized.message,
    statusCode: normalized.statusCode,
    details: normalized.details,
  }
}

export async function fetchFsList(uri: string, options: VikingListQueryOptions = {}): Promise<VikingListResult> {
  const normalizedUri = normalizeDirUri(uri)

  try {
    const result = await getOvResult(
      getFsLs({
        query: {
          uri: normalizedUri,
          output: options.output ?? 'agent',
          show_all_hidden: options.showAllHidden ?? true,
          node_limit: options.nodeLimit,
          limit: options.limit,
          abs_limit: options.absLimit,
          recursive: options.recursive,
          simple: options.simple,
        },
      }),
    )

    return {
      uri: normalizedUri,
      entries: normalizeFsEntries(result, normalizedUri),
    }
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchFsTree(rootUri: string, options: VikingTreeQueryOptions = {}): Promise<VikingTreeResult> {
  const normalizedRootUri = normalizeDirUri(rootUri)

  try {
    const result = await getOvResult(
      getFsTree({
        query: {
          uri: normalizedRootUri,
          output: options.output ?? 'agent',
          show_all_hidden: options.showAllHidden ?? true,
          node_limit: options.nodeLimit,
          limit: options.limit,
          abs_limit: options.absLimit,
          level_limit: options.levelLimit ?? 3,
        },
      }),
    )

    return {
      rootUri: normalizedRootUri,
      nodes: normalizeFsEntries(result, normalizedRootUri),
    }
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchFileContent(uri: string, options: VikingReadQueryOptions = {}): Promise<VikingReadResult> {
  const offset = options.offset ?? 0
  const limit = options.limit ?? -1

  try {
    const result = await getOvResult(
      getContentRead({
        query: {
          uri,
          offset,
          limit,
        },
      }),
    )

    const content = normalizeReadContent(result)

    return {
      uri,
      content,
      offset,
      limit,
      truncated: limit >= 0,
    }
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchFsStat(uri: string): Promise<VikingFsEntry> {
  try {
    const result = await getOvResult(
      getFsStat({ query: { uri } }),
    )
    const data = result as Record<string, unknown>
    const rawModTime = data.mod_time ?? data.modTime ?? data.modified_at ?? ''
    return {
      uri,
      name: fileNameFromUri(uri),
      isDir: Boolean(data.is_dir ?? data.isDir ?? uri.endsWith('/')),
      size: String(data.size ?? ''),
      sizeBytes: typeof data.size_bytes === 'number' ? data.size_bytes
        : typeof data.size === 'number' ? data.size : null,
      modTime: formatModTime(rawModTime),
      modTimestamp: null,
      abstract: String(data.abstract ?? ''),
    }
  } catch {
    return {
      uri,
      name: fileNameFromUri(uri),
      isDir: uri.endsWith('/'),
      size: '',
      sizeBytes: null,
      modTime: '',
      modTimestamp: null,
      abstract: '',
    }
  }
}

export async function saveFileContent(uri: string, content: string): Promise<void> {
  try {
    await getOvResult(
      postContentWrite({
        body: {
          uri,
          content,
          mode: 'replace',
          wait: true,
        },
      }),
    )
  } catch (error) {
    throw toVikingApiError(error)
  }
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

const FIND_CONTEXT_TYPES = ['resource', 'memory', 'skill'] as const

function isFindContextType(value: unknown): value is FindContextType {
  return FIND_CONTEXT_TYPES.some((type) => type === value)
}

function normalizeFindItems(value: unknown, fallbackType: FindContextType): FindResultItem[] {
  if (!Array.isArray(value)) return []

  return value
    .filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      uri: typeof item.uri === 'string' ? item.uri : '',
      context_type: isFindContextType(item.context_type) ? item.context_type : fallbackType,
      level: typeof item.level === 'number' ? item.level : 2,
      score: typeof item.score === 'number' ? item.score : 0,
      abstract: typeof item.abstract === 'string' ? item.abstract : '',
      overview: typeof item.overview === 'string' ? item.overview : null,
      category: typeof item.category === 'string' ? item.category : '',
      match_reason: typeof item.match_reason === 'string' ? item.match_reason : '',
      tags: typeof item.tags === 'string'
        ? item.tags
        : Array.isArray(item.tags)
          ? item.tags.filter((tag): tag is string => typeof tag === 'string').join(', ')
          : undefined,
      relations: Array.isArray(item.relations)
        ? item.relations
          .filter((relation): relation is Record<string, unknown> => relation !== null && typeof relation === 'object' && !Array.isArray(relation))
          .map((relation) => ({
            uri: typeof relation.uri === 'string' ? relation.uri : '',
            abstract: typeof relation.abstract === 'string' ? relation.abstract : '',
          }))
        : [],
    }))
}

function normalizeQueryPlan(value: unknown): FindQueryPlan | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'object' || Array.isArray(value)) return null

  const data = value as Record<string, unknown>
  const queries = Array.isArray(data.queries)
    ? data.queries
      .filter((query): query is Record<string, unknown> => query !== null && typeof query === 'object' && !Array.isArray(query))
      .map<FindQueryPlanItem>((query) => ({
        query: typeof query.query === 'string' ? query.query : '',
        context_type: isFindContextType(query.context_type) ? query.context_type : null,
        intent: typeof query.intent === 'string' ? query.intent : null,
        priority: typeof query.priority === 'number' ? query.priority : null,
      }))
      .filter((query) => query.query.trim().length > 0)
    : []

  return {
    reasoning: typeof data.reasoning === 'string' ? data.reasoning : null,
    queries,
  }
}

function normalizeGroupedFindResult(result: unknown): GroupedFindResult {
  const data = result !== null && typeof result === 'object' && !Array.isArray(result)
    ? result as Record<string, unknown>
    : {}
  const memories = normalizeFindItems(data.memories, 'memory')
  const resources = normalizeFindItems(data.resources, 'resource')
  const skills = normalizeFindItems(data.skills, 'skill')
  const total = typeof data.total === 'number' ? data.total : memories.length + resources.length + skills.length

  return {
    memories,
    resources,
    skills,
    total,
    query_plan: normalizeQueryPlan(data.query_plan),
    provenance: Array.isArray(data.provenance)
      ? data.provenance.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
      : null,
  }
}

export async function fetchFind(query: string, options: FetchFindOptions = {}): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult(
      postSearchFind({
        body: {
          query,
          target_uri: options.targetUri,
          limit: options.limit ?? 10,
          score_threshold: options.scoreThreshold,
          filter: options.filter,
        },
      }),
    )

    return normalizeGroupedFindResult(result)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchSearch(query: string, options: FetchSearchOptions = {}): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult(
      postSearchSearch({
        body: {
          query,
          target_uri: options.targetUri,
          session_id: options.sessionId,
          limit: options.limit ?? 10,
          score_threshold: options.scoreThreshold,
          filter: options.filter,
        },
      }),
    )

    return normalizeGroupedFindResult(result)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchFindAllTypes(query: string, options: Omit<FetchFindOptions, 'targetUri' | 'filter'> = {}): Promise<GroupedFindResult> {
  const results = await Promise.allSettled(
    FIND_CONTEXT_TYPES.map((ct) =>
      fetchFind(query, {
        ...options,
        filter: { op: 'must', field: 'context_type', conds: [ct] },
      }),
    ),
  )

  const merged: GroupedFindResult = { memories: [], resources: [], skills: [], total: 0 }

  const [resourceResult, memoryResult, skillResult] = results
  if (resourceResult.status === 'fulfilled') merged.resources = resourceResult.value.resources
  if (memoryResult.status === 'fulfilled') merged.memories = memoryResult.value.memories
  if (skillResult.status === 'fulfilled') merged.skills = skillResult.value.skills

  merged.total = merged.memories.length + merged.resources.length + merged.skills.length
  return merged
}
