import { useQuery, useMutation } from '@tanstack/react-query'
import { searchService, SearchResponse, SearchResult } from '../services/search'

// Query keys
export const SEARCH_QUERY_KEY = ['search']
export const SEARCH_FIND_KEY = (query: string) => [...SEARCH_QUERY_KEY, 'find', query]
export const SEARCH_GREP_KEY = (uri: string, pattern: string) => [...SEARCH_QUERY_KEY, 'grep', uri, pattern]

// Semantic search hook
export const useSearchFind = (query: string, limit: number = 10) => {
  return useQuery({
    queryKey: SEARCH_FIND_KEY(query),
    queryFn: () => searchService.find(query, { limit }),
    enabled: query.length > 0
  })
}

// Session-based search hook
export const useSearchInSession = (query: string, sessionId: string, limit: number = 10) => {
  return useQuery({
    queryKey: [...SEARCH_QUERY_KEY, 'session', sessionId, query],
    queryFn: () => searchService.search(query, sessionId, limit),
    enabled: !!query && !!sessionId
  })
}

// Grep search hook
export const useGrepSearch = (uri: string, pattern: string) => {
  return useQuery({
    queryKey: SEARCH_GREP_KEY(uri, pattern),
    queryFn: () => searchService.grep(uri, pattern),
    enabled: !!uri && !!pattern
  })
}

// Glob pattern hook
export const useGlobSearch = (pattern: string, uri: string = 'viking://') => {
  return useQuery({
    queryKey: [...SEARCH_QUERY_KEY, 'glob', pattern, uri],
    queryFn: () => searchService.glob(pattern, uri),
    enabled: !!pattern
  })
}

// Advanced search mutation (no caching)
export const useAdvancedSearch = () => {
  return useMutation({
    mutationFn: ({ query, filters }: { query: string; filters: any }) =>
      searchService.advancedSearch(query, filters)
  })
}

// Flatten search results utility
export const flattenSearchResults = (response: SearchResponse): SearchResult[] => {
  return [
    ...(response.resources || []),
    ...(response.memories || []),
    ...(response.skills || [])
  ]
}

export default useSearchFind
