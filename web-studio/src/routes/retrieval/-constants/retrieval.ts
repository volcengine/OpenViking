export const RESULT_COUNT_OPTIONS = [5, 10, 20, 50] as const
export const DEFAULT_RESULT_COUNT = 10

export const RETRIEVAL_MODES = [
  'find',
  'search',
  'recall',
  'grep',
  'glob',
] as const
export const DEFAULT_RETRIEVAL_MODE = 'find'

export const RETRIEVAL_SCOPES = ['all', 'resources', 'custom'] as const
export const DEFAULT_RETRIEVAL_SCOPE = 'all'

export const DEFAULT_CUSTOM_PATH_INPUT = 'resources/'
export const RECALL_MEMORY_TYPES = [
  'events',
  'entities',
  'preferences',
  'experiences',
] as const
export const DEFAULT_RECALL_QUOTAS = {
  entities: 10,
  events: 10,
  experiences: 0,
  preferences: 3,
} as const
export const DEFAULT_RECALL_MAX_CHARS = 6500
export const DEFAULT_RECALL_MIN_SCORE = 0.1
export const LAST_RETRIEVAL_SEARCH_KEY =
  'openviking.playground.retrieval.lastSearch'

export const KNOWN_VIKING_SCOPES = new Set([
  'agent',
  'resources',
  'session',
  'temp',
  'user',
])
export const LOADING_HINT_KEYS = [
  'loading.vector',
  'loading.scan',
  'loading.match',
  'loading.rerank',
] as const
