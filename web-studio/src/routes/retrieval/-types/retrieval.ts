import type {
  RESULT_COUNT_OPTIONS,
  RETRIEVAL_MODES,
  RETRIEVAL_SCOPES,
} from '../-constants/retrieval'
import type { FindContextType, FindResultItem } from '#/lib/retrieval'

export type RetrievalMode = (typeof RETRIEVAL_MODES)[number]
export type RetrievalScope = (typeof RETRIEVAL_SCOPES)[number]
export type ResultCountOption = (typeof RESULT_COUNT_OPTIONS)[number]
export type RetrievalTimeField = 'created_at' | 'updated_at'

export type RetrievalSearch = {
  q?: string
  mode?: RetrievalMode
  count?: number
  scope?: RetrievalScope
  path?: string
  session?: string
  ignoreCase?: boolean
  types?: string
  tags?: string
  minScore?: number
  levels?: string
  since?: string
  until?: string
  timeField?: RetrievalTimeField
  provenance?: boolean
}

export interface FlatRetrievalItem {
  type: FindContextType
  item: FindResultItem
  flatIndex: number
}

export interface RetrievalRequestOptions {
  contextTypes: FindContextType[]
  customPathInput: string
  ignoreCase: boolean
  includeProvenance: boolean
  levels: number[]
  resultCount: number
  scoreThreshold?: number
  sessionId?: string
  since?: string
  scope: RetrievalScope
  tags: string[]
  targetUri?: string
  timeField: RetrievalTimeField
  until?: string
}
