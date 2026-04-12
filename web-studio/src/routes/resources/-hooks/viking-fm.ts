import { useMemo, useRef, useCallback, useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchFileContent, fetchFind, fetchFindAllTypes, fetchFsList, fetchFsStat, fetchFsTree } from '../-lib/api'
import { detectFileType, normalizeDirUri, shouldAutoRead } from '../-lib/normalize'
import type {
  GroupedFindResult,
  VikingFsEntry,
  VikingListQueryOptions,
  VikingPreviewPolicy,
  VikingPreviewResult,
  VikingReadQueryOptions,
  VikingTreeQueryOptions,
} from '../-types/viking-fm'

const DEFAULT_QUERY_OPTS = { staleTime: 30_000 }

const PREFETCH_OPTS = { output: 'agent' as const, showAllHidden: true, nodeLimit: 200 }
const PREFETCH_STALE = 60_000

export function useVikingFsList(uri: string, options: VikingListQueryOptions = {}) {
  return useQuery({
    queryKey: ['viking-fs-ls', normalizeDirUri(uri), options],
    queryFn: () => fetchFsList(normalizeDirUri(uri), options),
    ...DEFAULT_QUERY_OPTS,
  })
}

export function useVikingFsTree(rootUri: string, options: VikingTreeQueryOptions = {}) {
  return useQuery({
    queryKey: ['viking-fs-tree', normalizeDirUri(rootUri), options],
    queryFn: () => fetchFsTree(normalizeDirUri(rootUri), options),
    ...DEFAULT_QUERY_OPTS,
  })
}

export function usePrefetchVikingFsList() {
  const client = useQueryClient()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const prefetch = useCallback((uri: string) => {
    const key = ['viking-fs-ls', normalizeDirUri(uri), PREFETCH_OPTS]
    if (client.getQueryState(key)?.data) return

    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      client.prefetchQuery({
        queryKey: key,
        queryFn: () => fetchFsList(normalizeDirUri(uri), PREFETCH_OPTS),
        staleTime: PREFETCH_STALE,
      })
    }, 200)
  }, [client])

  return { prefetch }
}

export function useVikingFilePreview(
  entry: VikingFsEntry | null,
  policy: VikingPreviewPolicy = {},
  readOptions: VikingReadQueryOptions = {},
) {
  const maxAutoReadBytes = policy.maxAutoReadBytes ?? 2 * 1024 * 1024
  const defaultReadLimit = policy.defaultReadLimit ?? 500
  const effectiveReadOptions = useMemo(() => ({
    offset: readOptions.offset ?? 0,
    limit: readOptions.limit ?? defaultReadLimit,
  }), [readOptions.offset, readOptions.limit, defaultReadLimit])

  const autoRead = useMemo(
    () =>
      entry
        ? shouldAutoRead(entry, maxAutoReadBytes)
        : { shouldRead: false as const },
    [entry, maxAutoReadBytes],
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
        content: '',
        offset: effectiveReadOptions.offset,
        limit: effectiveReadOptions.limit,
        truncated: true,
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
    canLoadContent: Boolean(entry) && autoRead.shouldRead,
  }
}

export function useInvalidateVikingFs() {
  const queryClient = useQueryClient()

  return {
    invalidateAll: () =>
      queryClient.invalidateQueries({ queryKey: ['viking-fs'] }),
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
    invalidatePreview: (uri?: string) =>
      queryClient.invalidateQueries({
        queryKey: uri ? ['viking-file-read', uri] : ['viking-file-read'],
      }),
  }
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function useVikingFind(query: string, targetUri?: string) {
  const debouncedQuery = useDebouncedValue(query, 300)
  const isRoot = !targetUri || targetUri === 'viking://'
  return useQuery<GroupedFindResult>({
    queryKey: ['viking-find', debouncedQuery, targetUri],
    queryFn: () =>
      isRoot
        ? fetchFindAllTypes(debouncedQuery)
        : fetchFind(debouncedQuery, { targetUri }),
    enabled: debouncedQuery.trim().length > 0,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  })
}

export function useVikingFsStat(uri: string | undefined) {
  return useQuery<VikingFsEntry>({
    queryKey: ['viking-fs-stat', uri],
    queryFn: () => fetchFsStat(uri!),
    enabled: Boolean(uri),
    staleTime: 60_000,
  })
}
