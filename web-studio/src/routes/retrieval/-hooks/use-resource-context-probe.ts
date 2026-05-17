import { useQuery } from '@tanstack/react-query'

import { getFsLs, getOvResult } from '#/lib/ov-client'

type ResourceProbeResult = {
  hasContext: boolean
}

function normalizeHasEntries(result: unknown): boolean {
  if (Array.isArray(result)) {
    return result.length > 0
  }

  if (result !== null && typeof result === 'object' && !Array.isArray(result)) {
    const record = result as Record<string, unknown>
    const buckets = [record.entries, record.items, record.children, record.results, record.nodes]
    return buckets.some((bucket) => Array.isArray(bucket) && bucket.length > 0)
  }

  return false
}

export function useResourceContextProbe() {
  return useQuery<ResourceProbeResult>({
    queryFn: async () => {
      const result = await getOvResult(
        getFsLs({
          query: {
            node_limit: 1,
            output: 'agent',
            recursive: true,
            show_all_hidden: false,
            uri: 'viking://resources/',
          },
        }),
      )

      return { hasContext: normalizeHasEntries(result) }
    },
    queryKey: ['retrieval-resource-context-probe'],
    staleTime: 30_000,
  })
}
