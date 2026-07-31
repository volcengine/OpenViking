import { useQuery } from '@tanstack/react-query'

import { fetchFind, fetchGlob, fetchGrep, fetchSearch } from '#/lib/retrieval'
import type { GroupedFindResult } from '#/lib/retrieval'

import type {
  RetrievalMode,
  RetrievalRequestOptions,
} from '../-types/retrieval'

export function useRetrievalQuery({
  enabled,
  mode,
  query,
  options,
}: {
  enabled: boolean
  mode: RetrievalMode
  query: string
  options: RetrievalRequestOptions
}) {
  return useQuery<GroupedFindResult>({
    enabled,
    gcTime: 5 * 60_000,
    queryFn: () => {
      if (mode === 'search') {
        return fetchSearch(query, {
          contextTypes: options.contextTypes,
          includeProvenance: options.includeProvenance,
          levels: options.levels,
          limit: options.resultCount,
          scoreThreshold: options.scoreThreshold,
          sessionId: options.sessionId,
          since: options.since,
          tags: options.tags,
          targetUri: options.targetUri,
          timeField: options.timeField,
          until: options.until,
        })
      }

      if (mode === 'grep') {
        return fetchGrep(query, {
          caseInsensitive: options.ignoreCase,
          limit: options.resultCount,
          uri: options.targetUri ?? 'viking://',
        })
      }

      if (mode === 'glob') {
        return fetchGlob(query, {
          limit: options.resultCount,
          uri: options.targetUri ?? 'viking://',
        })
      }

      return fetchFind(query, {
        contextTypes: options.contextTypes,
        includeProvenance: options.includeProvenance,
        levels: options.levels,
        limit: options.resultCount,
        scoreThreshold: options.scoreThreshold,
        since: options.since,
        tags: options.tags,
        targetUri: options.targetUri,
        timeField: options.timeField,
        until: options.until,
      })
    },
    queryKey: ['retrieval', mode, query, options],
    staleTime: 60_000,
  })
}
