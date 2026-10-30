import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'

import {
  fetchFileContent,
  fetchFsList,
  fetchFsStat,
  fetchFsTree,
} from '#/features/workbench/resources/lib/api'
import {
  detectFileType,
  normalizeDirUri,
  shouldAutoRead,
} from '#/shared/lib/viking-fs'
import type {
  VikingFsEntry,
  VikingListQueryOptions,
  VikingPreviewPolicy,
  VikingPreviewResult,
  VikingReadQueryOptions,
  VikingTreeQueryOptions,
} from '#/shared/types/viking-fs'

const DEFAULT_QUERY_OPTS = { staleTime: 30_000 }

export function useVikingFsList(
  uri: string,
  options: VikingListQueryOptions = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ['viking-fs-ls', normalizeDirUri(uri), options],
    queryFn: () => fetchFsList(normalizeDirUri(uri), options),
    enabled,
    ...DEFAULT_QUERY_OPTS,
  })
}

export function useVikingFsTree(
  rootUri: string,
  options: VikingTreeQueryOptions = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ['viking-fs-tree', normalizeDirUri(rootUri), options],
    queryFn: () => fetchFsTree(normalizeDirUri(rootUri), options),
    enabled,
    ...DEFAULT_QUERY_OPTS,
  })
}

export function useVikingFilePreview(
  entry: VikingFsEntry | null,
  policy: VikingPreviewPolicy = {},
  readOptions: VikingReadQueryOptions = {},
) {
  const maxAutoReadBytes = policy.maxAutoReadBytes ?? 2 * 1024 * 1024
  const defaultReadLimit = policy.defaultReadLimit ?? 500
  const requireKnownSize = policy.requireKnownSize ?? false
  const effectiveReadOptions = useMemo(
    () => ({
      offset: readOptions.offset ?? 0,
      limit: readOptions.limit ?? defaultReadLimit,
      raw: readOptions.raw,
    }),
    [readOptions.offset, readOptions.limit, readOptions.raw, defaultReadLimit],
  )

  const autoRead = useMemo(
    () =>
      entry
        ? shouldAutoRead(entry, maxAutoReadBytes, requireKnownSize)
        : { shouldRead: false as const },
    [entry, maxAutoReadBytes, requireKnownSize],
  )

  const readQuery = useQuery({
    enabled: Boolean(entry) && autoRead.shouldRead,
    queryKey: [
      'viking-file-read',
      entry?.uri,
      entry?.modTime || '',
      effectiveReadOptions,
    ],
    queryFn: () => fetchFileContent(entry!.uri, effectiveReadOptions),
  })

  const preview = useMemo<VikingPreviewResult | null>(() => {
    if (!entry) {
      return null
    }

    const fileType = detectFileType(entry.uri)

    if (!autoRead.shouldRead) {
      return {
        entry,
        fileType,
        shouldAutoRead: false,
        reason: autoRead.reason,
        content: readQuery.data?.content || '',
        offset: readQuery.data?.offset ?? effectiveReadOptions.offset,
        limit: readQuery.data?.limit ?? effectiveReadOptions.limit,
        truncated: readQuery.data?.truncated ?? true,
      }
    }

    return {
      entry,
      fileType,
      shouldAutoRead: true,
      content: readQuery.data?.content || '',
      offset: readQuery.data?.offset ?? effectiveReadOptions.offset,
      limit: readQuery.data?.limit ?? effectiveReadOptions.limit,
      truncated: readQuery.data?.truncated ?? true,
    }
  }, [entry, autoRead, readQuery.data, effectiveReadOptions])

  return {
    ...readQuery,
    preview,
    isContentLoaded: readQuery.data !== undefined,
    canLoadContent:
      Boolean(entry) && !autoRead.shouldRead && autoRead.reason !== 'binary',
  }
}

export function useInvalidateVikingFs() {
  const queryClient = useQueryClient()
  const invalidateAll = useCallback(
    () =>
      queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === 'viking-fs-ls' ||
          queryKey[0] === 'viking-fs-tree' ||
          queryKey[0] === 'viking-fs-stat',
      }),
    [queryClient],
  )
  const invalidatePreview = useCallback(
    (uri?: string) =>
      queryClient.invalidateQueries({
        queryKey: uri ? ['viking-file-read', uri] : ['viking-file-read'],
      }),
    [queryClient],
  )

  return {
    invalidateAll,
    invalidateList: (uri?: string) =>
      queryClient.invalidateQueries({
        queryKey: uri
          ? ['viking-fs-ls', normalizeDirUri(uri)]
          : ['viking-fs-ls'],
      }),
    invalidateTree: (uri?: string) =>
      queryClient.invalidateQueries({
        queryKey: uri
          ? ['viking-fs-tree', normalizeDirUri(uri)]
          : ['viking-fs-tree'],
      }),
    invalidatePreview,
  }
}

export function useVikingFsStat(uri: string | undefined) {
  return useQuery<VikingFsEntry>({
    queryKey: ['viking-fs-stat', uri],
    queryFn: () => fetchFsStat(uri!),
    enabled: Boolean(uri),
    staleTime: 60_000,
  })
}
