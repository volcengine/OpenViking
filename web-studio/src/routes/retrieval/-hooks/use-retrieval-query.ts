import { useQuery } from '@tanstack/react-query'

import { fetchFind, fetchGlob, fetchGrep, fetchSearch } from '#/lib/retrieval'
import type { GroupedFindResult } from '#/lib/retrieval'

import type { RetrievalMode } from '../-types/retrieval'

export function useRetrievalQuery({
  enabled,
  ignoreCase,
  mode,
  query,
  resultCount,
  sessionId,
  targetUri,
}: {
  enabled: boolean
  ignoreCase: boolean
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

      if (mode === 'grep') {
        return fetchGrep(query, {
          caseInsensitive: ignoreCase,
          limit: resultCount,
          uri: targetUri ?? 'viking://',
        })
      }

      if (mode === 'glob') {
        return fetchGlob(query, {
          limit: resultCount,
          uri: targetUri ?? 'viking://',
        })
      }

      return fetchFind(query, { limit: resultCount, targetUri })
    },
    queryKey: [
      'retrieval',
      mode,
      query,
      targetUri,
      resultCount,
      sessionId,
      ignoreCase,
    ],
    staleTime: 60_000,
  })
}
