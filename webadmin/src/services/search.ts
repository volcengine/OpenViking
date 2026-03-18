import apiClient, { handleAPI, APIResponse } from './api'

// Search types
export interface SearchResult {
  uri: string
  context_type: string
  level: number
  abstract: string
  score: number
  content?: string
}

export interface SearchOptions {
  limit?: number
  target_uri?: string
  session_id?: string
}

// Backend grep response format
export interface BackendGrepMatch {
  line: number
  uri: string
  content: string
}

// Frontend normalized grep format
export interface GrepMatch {
  uri: string
  line_number: number
  line: string
  pattern: string
}

export interface SearchResponse {
  resources: SearchResult[]
  memories: SearchResult[]
  skills: SearchResult[]
  total: number
}

// Normalize backend grep match format
const normalizeGrepMatch = (backend: BackendGrepMatch): GrepMatch => ({
  uri: backend.uri,
  line_number: backend.line,
  line: backend.content,
  pattern: ''
})

// Search service
export const searchService = {
  /**
   * Semantic search
   */
  find: async (
    query: string,
    options: SearchOptions = {}
  ): Promise<APIResponse<SearchResponse>> => {
    const { limit = 10, target_uri } = options

    return handleAPI<SearchResponse>(
      apiClient.post('/search/find', { query, limit, target_uri })
    )
  },

  /**
   * Search within a session context
   */
  search: async (
    query: string,
    session_id: string,
    limit: number = 10
  ): Promise<APIResponse<SearchResponse>> => {
    return handleAPI<SearchResponse>(
      apiClient.post('/search/search', { query, session_id, limit })
    )
  },

  /**
   * Grep search (regex pattern matching)
   */
  grep: async (
    uri: string,
    pattern: string
  ): Promise<APIResponse<GrepMatch[]>> => {
    return handleAPI<{ matches: BackendGrepMatch[] }>(
      apiClient.post('/search/grep', { uri, pattern })
    ).then(res => ({
      ...res,
      data: res.data?.matches ? res.data.matches.map(normalizeGrepMatch) : undefined
    }))
  },

  /**
   * Glob pattern matching
   */
  glob: async (
    pattern: string,
    uri: string = 'viking://'
  ): Promise<APIResponse<string[]>> => {
    return handleAPI<string[]>(
      apiClient.post('/search/glob', { pattern, uri })
    )
  },

  /**
   * Extract all search results into a flat array
   */
  flattenResults: (response: SearchResponse): SearchResult[] => {
    return [
      ...(response.resources || []),
      ...(response.memories || []),
      ...(response.skills || [])
    ]
  },

  /**
   * Search with filters
   */
  advancedSearch: async (
    query: string,
    filters: {
      types?: string[]
      uris?: string[]
      limit?: number
    }
  ): Promise<APIResponse<SearchResponse>> => {
    const response = await searchService.find(query, {
      limit: filters.limit || 10
    })

    if (!response.success || !response.data) {
      return response
    }

    let results = searchService.flattenResults(response.data)

    // Apply filters
    if (filters.types && filters.types.length > 0) {
      results = results.filter(r => filters.types!.includes(r.context_type))
    }

    if (filters.uris && filters.uris.length > 0) {
      results = results.filter(r => filters.uris!.includes(r.uri))
    }

    return {
      success: true,
      data: {
        resources: [],
        memories: [],
        skills: [],
        total: results.length
      }
    }
  }
}

export default searchService
