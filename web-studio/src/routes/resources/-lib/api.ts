import { getContentRead, getFsLs, getFsTree, getOvResult, normalizeOvClientError } from '#/lib/ov-client'

import { normalizeDirUri, normalizeFsEntries, normalizeReadContent } from './normalize'
import type {
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
