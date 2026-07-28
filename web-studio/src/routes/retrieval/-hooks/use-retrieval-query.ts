import { useQuery } from '@tanstack/react-query'

import {
  fetchFind,
  fetchGlob,
  fetchGrep,
  fetchRecall,
  fetchSearch,
} from '#/lib/retrieval'
import type { GroupedFindResult, RecallResult } from '#/lib/retrieval'

import type {
  RetrievalMode,
  RetrievalRequestOptions,
} from '../-types/retrieval'

export type RetrievalQueryResult =
  | { kind: 'recall'; result: RecallResult }
  | { kind: 'results'; result: GroupedFindResult }

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
  return useQuery<RetrievalQueryResult>({
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
        }).then((result) => ({ kind: 'results' as const, result }))
      }

      if (mode === 'recall') {
        return fetchRecall(query, {
          maxChars: options.recallMaxChars,
          minScore: options.recallMinScore,
          peerScope: options.recallPeerScope,
          quotas: options.recallQuotas,
          render: options.recallRender,
        }).then((result) => ({ kind: 'recall' as const, result }))
      }

      if (mode === 'grep') {
        return fetchGrep(query, {
          caseInsensitive: options.ignoreCase,
          limit: options.resultCount,
          uri: options.targetUri ?? 'viking://',
        }).then((result) => ({ kind: 'results' as const, result }))
      }

      if (mode === 'glob') {
        return fetchGlob(query, {
          limit: options.resultCount,
          uri: options.targetUri ?? 'viking://',
        }).then((result) => ({ kind: 'results' as const, result }))
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
      }).then((result) => ({ kind: 'results' as const, result }))
    },
    queryKey: ['retrieval', mode, query, options],
    staleTime: 60_000,
  })
}
