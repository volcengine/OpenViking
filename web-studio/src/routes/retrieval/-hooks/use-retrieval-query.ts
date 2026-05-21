import { useQuery } from '@tanstack/react-query'

import { fetchFind, fetchFindAllTypes, fetchSearch } from '#/lib/retrieval'
import type { GroupedFindResult } from '#/lib/retrieval'

import type { RetrievalMode } from '../-types/retrieval'

export function useRetrievalQuery({
  enabled,
  mode,
  query,
  resultCount,
  sessionId,
  targetUri,
}: {
  enabled: boolean
  mode: RetrievalMode
  query: string
  resultCount: number
  sessionId?: string
  targetUri?: string
}) {
  return useQuery<GroupedFindResult>({
    enabled,
    gcTime: 5 * 60_000,
    placeholderData: (prev) => prev,
    queryFn: () => {
      if (mode === 'search') {
        return fetchSearch(query, { limit: resultCount, sessionId, targetUri })
      }

      return targetUri
        ? fetchFind(query, { limit: resultCount, targetUri })
        : fetchFindAllTypes(query, { limit: resultCount })
    },
    queryKey: ['retrieval', mode, query, targetUri, resultCount, sessionId],
    staleTime: 60_000,
  })
}
