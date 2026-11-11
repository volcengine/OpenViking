import { useQueries, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import type { FileTreeRow } from '@openviking/react/components/file-tree'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ROOT_URI } from './constants'
import { getOvClient } from '#/shared/api/ov-client'
import {
  fileNameFromUri,
  normalizeDirUri,
  parentUri,
} from '#/shared/lib/viking-fs'

const PAGE_SIZE = 200

interface TreeEntry {
  uri: string
  isDir?: boolean
  access?: 'read-only' | 'denied' | 'container'
  permission?: string
}

async function fetchContextTreePage(
  uri: string,
  offset: number,
  signal: AbortSignal,
) {
  const response = await getOvClient().instance.get<{
    result?: TreeEntry[]
    has_more?: boolean
    error?: { message?: string }
    status?: string
  }>('/api/v1/fs/ls', {
    params: {
      uri,
      output: 'agent',
      include_abstract: false,
      node_limit: PAGE_SIZE,
      offset,
    },
    signal,
  })
  if (response.data.status === 'error')
    throw new Error(response.data.error?.message)
  return {
    entries: response.data.result ?? [],
    hasMore: Boolean(response.data.has_more),
  }
}

export function useContextTree(rootUri: string, expandedKeys: Set<string>) {
  const { identityScopeKey } = useAppConnection()
  const client = useQueryClient()
  const [pages, setPages] = useState<Record<string, number>>({})
  const root = normalizeDirUri(rootUri)
  const expanded = new Set([...expandedKeys].map(normalizeDirUri))
  const isVisible = (uri: string) => {
    if (uri === root || !uri.startsWith(root)) return false
    for (
      let parent = parentUri(uri);
      parent !== root;
      parent = parentUri(parent)
    ) {
      if (!expanded.has(parent)) return false
    }
    return true
  }
  const directories = [root, ...[...expanded].filter(isVisible)]
  const requests = directories.flatMap((uri) =>
    Array.from({ length: pages[uri] ?? 1 }, (_, page) => ({ uri, page })),
  )
  const queries = useQueries({
    queries: requests.map(({ uri, page }) => ({
      queryKey: ['viking-fs-ls', uri, 'context-tree', identityScopeKey, page],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchContextTreePage(uri, page * PAGE_SIZE, signal),
      staleTime: 30_000,
    })),
  })
  const byDirectory = new Map<string, typeof queries>()
  requests.forEach(({ uri }, index) => {
    const group = byDirectory.get(uri) ?? []
    group.push(queries[index])
    byDirectory.set(uri, group)
  })
  const rows: FileTreeRow[] = []
  const rootQuery = queries[0]
  function append(uri: string, depth: number) {
    const directory = byDirectory.get(uri)
    if (!directory) return
    const entries = directory.flatMap((query) => query.data?.entries ?? [])
    if (uri === ROOT_URI) {
      const userIndex = entries.findIndex(
        (entry) => normalizeDirUri(entry.uri) === 'viking://user/',
      )
      if (userIndex > 0) entries.unshift(...entries.splice(userIndex, 1))
    }
    const seen = new Set<string>()
    for (const entry of entries) {
      const child = normalizeDirUri(entry.uri)
      const isExpanded = Boolean(
        entry.isDir && child !== uri && expanded.has(child),
      )
      if (seen.has(entry.uri) || child === uri) continue
      seen.add(entry.uri)
      rows.push({
        kind: 'entry',
        id: entry.uri.replace(/\/$/, ''),
        depth,
        uri: entry.uri,
        name: fileNameFromUri(entry.uri),
        type: entry.isDir ? 'directory' : 'file',
        expanded: isExpanded,
        disabled: entry.access === 'denied',
        selectable: entry.access !== 'container',
        access:
          entry.access ??
          (entry.permission === 'read' ? 'read-only' : undefined),
      })
      if (isExpanded && child.startsWith(uri)) append(child, depth + 1)
    }
    const last = directory.at(-1)
    if (last?.isError && (depth > 0 || directory.length > 1)) {
      rows.push({
        kind: 'error',
        id: `${uri}:error`,
        parentId: uri,
        depth,
        message: last.error.message,
      })
    } else if (last?.isPending && depth > 0) {
      rows.push({ kind: 'loading', id: `${uri}:loading`, parentId: uri, depth })
    } else if (last?.isSuccess && depth > 0 && entries.length === 0) {
      rows.push({ kind: 'empty', id: `${uri}:empty`, parentId: uri, depth })
    } else if (last?.data?.hasMore) {
      rows.push({ kind: 'load-more', id: `${uri}:more`, parentId: uri, depth })
    }
  }
  if (rootQuery.isError && !rootQuery.data) {
    rows.push({
      kind: 'error',
      id: `${root}:error`,
      depth: 0,
      message: rootQuery.error.message,
    })
  } else {
    append(root, 0)
  }
  const refresh = useCallback(
    () =>
      client.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === 'viking-fs-ls' &&
          queryKey[2] === 'context-tree' &&
          queryKey[3] === identityScopeKey,
      }),
    [client, identityScopeKey],
  )
  return {
    rows,
    isReady: !rootQuery.isPending,
    refresh,
    loadMore: async (uri: string) =>
      setPages((current) => ({ ...current, [uri]: (current[uri] ?? 1) + 1 })),
    retry: async (uri: string) => {
      await byDirectory.get(uri)?.at(-1)?.refetch()
    },
  }
}
