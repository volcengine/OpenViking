import { getContentRead, getFsLs, getFsTree, getOvResult, normalizeOvClientError, postSearchFind } from '#/lib/ov-client'

import { normalizeDirUri, normalizeFsEntries, normalizeReadContent } from './normalize'
import type {
  GroupedFindResult,
  VikingApiError,
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

export interface FetchFindOptions {
  targetUri?: string
  limit?: number
  scoreThreshold?: number
  filter?: Record<string, unknown>
}

export async function fetchFind(query: string, options: FetchFindOptions = {}): Promise<GroupedFindResult> {
  try {
    const result = await getOvResult(
      postSearchFind({
        body: {
          query,
          target_uri: options.targetUri,
          limit: options.limit ?? 50,
          score_threshold: options.scoreThreshold,
          filter: options.filter,
        },
      }),
    )

    const data = result as Record<string, unknown>
    return {
      memories: Array.isArray(data.memories) ? data.memories : [],
      resources: Array.isArray(data.resources) ? data.resources : [],
      skills: Array.isArray(data.skills) ? data.skills : [],
      total: typeof data.total === 'number' ? data.total : 0,
    }
  } catch (error) {
    throw toVikingApiError(error)
  }
}

const FIND_CONTEXT_TYPES = ['resource', 'memory', 'skill'] as const

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

  for (const [i, r] of results.entries()) {
    if (r.status !== 'fulfilled') continue
    const ct = FIND_CONTEXT_TYPES[i]
    if (ct === 'resource') merged.resources = r.value.resources
    else if (ct === 'memory') merged.memories = r.value.memories
    else if (ct === 'skill') merged.skills = r.value.skills
  }

  merged.total = merged.memories.length + merged.resources.length + merged.skills.length
  return merged
}
