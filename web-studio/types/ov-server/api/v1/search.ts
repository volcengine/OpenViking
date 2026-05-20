import type { JsonObject } from '../../common'

export type FindContextType = 'memory' | 'resource' | 'skill'

export type SearchRelation = {
  abstract: string
  uri: string
}

export type SearchHit = {
  abstract?: string
  category?: string
  context_type?: FindContextType | (string & {})
  level?: number
  match_reason?: string
  overview?: string | null
  relations?: SearchRelation[]
  score?: number
  uri?: string
}

export type SearchQueryPlanItem = {
  context_type?: FindContextType | null
  intent?: string | null
  priority?: number | null
  query: string
}

export type SearchQueryPlan = {
  queries: SearchQueryPlanItem[]
  reasoning?: string | null
}

export type SearchResult = {
  memories?: SearchHit[]
  provenance?: JsonObject[] | null
  query_plan?: SearchQueryPlan | null
  resources?: SearchHit[]
  skills?: SearchHit[]
  total?: number
}

export type FindResult = SearchResult
