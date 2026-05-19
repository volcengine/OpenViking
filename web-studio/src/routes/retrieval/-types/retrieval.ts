import type {
  RESULT_COUNT_OPTIONS,
  RETRIEVAL_MODES,
  RETRIEVAL_SCOPES,
} from '../-constants/retrieval'
import type { FindContextType, FindResultItem } from '#/lib/retrieval'

export type RetrievalMode = (typeof RETRIEVAL_MODES)[number]
export type RetrievalScope = (typeof RETRIEVAL_SCOPES)[number]
export type ResultCountOption = (typeof RESULT_COUNT_OPTIONS)[number]

export type RetrievalSearch = {
  q?: string
  mode?: RetrievalMode
  count?: number
  scope?: RetrievalScope
  path?: string
  session?: string
}

export interface FlatRetrievalItem {
  type: FindContextType
  item: FindResultItem
  flatIndex: number
}
