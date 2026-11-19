import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import {
  fetchFsList,
  fetchFsStat,
  fetchFsTree,
} from '#/features/workbench/resources/lib/api'
import { normalizeDirUri } from '#/shared/lib/viking-fs'
import type {
  VikingFsEntry,
  VikingListQueryOptions,
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

export function useInvalidateVikingFs() {
  const queryClient = useQueryClient()
  const invalidateAll = useCallback(
    () =>
      queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === 'viking-fs-ls' ||
          queryKey[0] === 'viking-fs-tree' ||
          queryKey[0] === 'viking-fs-stat' ||
          queryKey[0] === 'studio-shared-preview',
      }),
    [queryClient],
  )
  const invalidatePreview = useCallback(
    (uri?: string) =>
      queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === 'studio-shared-preview' &&
          (!uri || queryKey[2] === uri),
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
