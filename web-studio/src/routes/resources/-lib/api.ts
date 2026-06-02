import { fetchFind, fetchFindAllTypes, fetchSearch } from '#/lib/retrieval'
import {
  getContentRead,
  getContentAbstract,
  getContentOverview,
  getFsLs,
  getFsStat,
  getFsTree,
  getOvResult,
  normalizeOvClientError,
  postContentWrite,
} from '#/lib/ov-client'
import type {
  ContentReadResult,
  ContentWriteResult,
} from '@ov-server/api/v1/content'
import type {
  FSListResult,
  FSStatResult,
  FSTreeResult,
} from '@ov-server/api/v1/fs'

import {
  fileNameFromUri,
  formatModTime,
  normalizeDirUri,
  normalizeFsEntries,
  normalizeReadContent,
} from './normalize'
import type {
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

export async function fetchFsList(
  uri: string,
  options: VikingListQueryOptions = {},
): Promise<VikingListResult> {
  const normalizedUri = normalizeDirUri(uri)

  try {
    const result = await getOvResult<FSListResult>(
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

export async function fetchFsTree(
  rootUri: string,
  options: VikingTreeQueryOptions = {},
): Promise<VikingTreeResult> {
  const normalizedRootUri = normalizeDirUri(rootUri)

  try {
    const result = await getOvResult<FSTreeResult>(
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

export async function fetchFileContent(
  uri: string,
  options: VikingReadQueryOptions = {},
): Promise<VikingReadResult> {
  const offset = options.offset ?? 0
  const limit = options.limit ?? -1

  try {
    const result = await getOvResult<ContentReadResult>(
      getContentRead({
        query: {
          uri,
          offset,
          limit,
          raw: options.raw,
        } as Parameters<typeof getContentRead>[0]['query'] & { raw?: boolean },
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

export async function fetchDirectoryLevelContent(
  uri: string,
  level: 'abstract' | 'overview',
): Promise<string> {
  try {
    const request =
      level === 'abstract'
        ? getContentAbstract({ query: { uri: normalizeDirUri(uri) } })
        : getContentOverview({ query: { uri: normalizeDirUri(uri) } })
    const result = await getOvResult<unknown>(request)
    return normalizeReadContent(result)
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export async function fetchFsStat(
  uri: string,
  options: { throwOnError?: boolean } = {},
): Promise<VikingFsEntry> {
  try {
    const result = await getOvResult<FSStatResult>(
      getFsStat({ query: { uri } }),
    )
    const data = result as Record<string, unknown>
    const rawModTime = data.mod_time ?? data.modTime ?? data.modified_at ?? ''
    return {
      uri,
      name: fileNameFromUri(uri),
      isDir: Boolean(data.is_dir ?? data.isDir ?? uri.endsWith('/')),
      size: String(data.size ?? ''),
      sizeBytes:
        typeof data.size_bytes === 'number'
          ? data.size_bytes
          : typeof data.size === 'number'
            ? data.size
            : null,
      modTime: formatModTime(rawModTime),
      modTimestamp: null,
      abstract: String(data.abstract ?? ''),
      overview: String(data.overview ?? ''),
    }
  } catch (error) {
    // Callers that need to surface read failures (e.g. clicking a missing or
    // unauthorized URI) opt in; the default keeps the lenient synthetic entry.
    if (options.throwOnError) throw toVikingApiError(error)
    return {
      uri,
      name: fileNameFromUri(uri),
      isDir: uri.endsWith('/'),
      size: '',
      sizeBytes: null,
      modTime: '',
      modTimestamp: null,
      abstract: '',
      overview: '',
    }
  }
}

export async function saveFileContent(
  uri: string,
  content: string,
): Promise<void> {
  try {
    await getOvResult<ContentWriteResult>(
      postContentWrite({
        body: {
          uri,
          content,
          mode: 'replace',
          wait: false,
        },
      }),
    )
  } catch (error) {
    throw toVikingApiError(error)
  }
}

export { fetchFind, fetchFindAllTypes, fetchSearch }
