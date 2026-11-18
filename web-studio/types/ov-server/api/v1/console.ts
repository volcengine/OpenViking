import type {
  OvDisabledResult,
  OvMaybeDisabled,
  PaginatedResult,
} from '../../common'

export type ConsoleContextCounts = {
  files?: number
  memories?: number
  skills?: number
  total?: number
}

export type ConsoleTokenCounts = {
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

export type ConsoleRetrievalCounts = {
  find?: number
  search?: number
  total?: number
}

export type ConsoleDashboardSummaryResult = OvMaybeDisabled & {
  context_counts?: ConsoleContextCounts
  today_retrievals?: ConsoleRetrievalCounts
  today_tokens?: ConsoleTokenCounts
}

export type ConsoleSeriesBucket = 'day' | '4h' | (string & {})

export type ConsoleSeriesQuery = {
  bucket?: ConsoleSeriesBucket
  end_date: string
  start_date: string
  timezone?: string | null
}

export type ConsoleDashboardSummaryQuery = {
  timezone?: string | null
}

export type ConsoleSeriesResult<TItem> = OvMaybeDisabled & {
  bucket?: string
  end_date?: string
  items?: TItem[]
  start_date?: string
}

export type ConsoleTokenSeriesItem = {
  date?: string
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

export type ConsoleTokenSeriesResult =
  ConsoleSeriesResult<ConsoleTokenSeriesItem>

export type ConsoleContextCommitItem = {
  add_resource?: number
  add_skill?: number
  date?: string
  hour?: number
  session_add_message?: number
  session_commit?: number
  total?: number
}

export type ConsoleContextCommitsResult =
  ConsoleSeriesResult<ConsoleContextCommitItem>

export type ConsoleAuditLogItem = {
  account_id?: string | null
  api_type?: string
  created_at?: string
  duration_ms?: number
  error_code?: string | null
  error_details?: Record<string, unknown> | null
  error_message?: string | null
  method?: string
  request_id?: string | null
  route?: string
  status_code?: number
  user_id?: string | null
}

export type ConsoleAuditResult = OvMaybeDisabled &
  PaginatedResult<ConsoleAuditLogItem> & {
    success_rate?: number
  }

export type ConsoleAuditQuery = {
  api_type?: string[] | null
  page?: number
  page_size?: number
  request_id?: string | null
  status?: string[] | null
}

export type ConsoleDisabledResult = OvDisabledResult

export type PerformanceTokenMetric = {
  known_tokens: number
  unknown_events: number
}
export type PerformanceSummary = {
  observations: number
  observed_model_events: number
  unverified_usage_events: number
  unknown_usage_events: number
  attempt_observations: number
  adapter_observations: number
  failed_observations: number
  cancelled_observations: number
  token_totals: Record<
    'input_tokens' | 'output_tokens' | 'cached_tokens' | 'reasoning_tokens',
    PerformanceTokenMetric
  >
  reported_token_totals: Record<
    'input_tokens' | 'output_tokens' | 'cached_tokens' | 'reasoning_tokens',
    number
  >
  estimated_token_totals: Record<
    'input_tokens' | 'output_tokens' | 'cached_tokens' | 'reasoning_tokens',
    number
  >
  unverified_token_totals: Record<
    'input_tokens' | 'output_tokens' | 'cached_tokens' | 'reasoning_tokens',
    number
  >
  incomplete_usage_events: number
  duration_by_kind: Record<
    string,
    { observed_span_seconds: number; unknown_duration_observations: number }
  >
  cache_hit_rate: number | null
  cache_covered_events: number
  observed_interval_union_seconds: number
  timed_interval_observations: number
}
export type PerformanceWorkflow = PerformanceSummary & {
  user_id: string
  workflow_id: string
  position: string
}
export type PerformanceObservation = {
  event_id: string
  event_name: string
  user_id: string
  workflow_id: string
  task_id: string
  work_id: string
  request_id: string
  stage_id: string
  parent_stage_id: string
  stage: string
  model: string
  provider: string
  status: string
  created_at: string
  recorded_at: string
  duration_seconds: number | null
  input_tokens: number | null
  output_tokens: number | null
  cached_tokens: number | null
  reasoning_tokens: number | null
  usage_quality: string
}
export type PerformanceQuery = {
  start: string
  end: string
  view?: 'summary' | 'series' | 'workflows' | 'details'
  workflow_id?: string
  user_id?: string
  model?: string
  status?: string
  task_id?: string
  request_id?: string
  cursor?: string
  observed_before?: string
  page_size?: number
}
export type PerformanceResult<T> = {
  enabled?: boolean
  unsupported?: boolean
  observed_before?: string
  start?: string
  end?: string
  coverage?: 'observed' | 'truncated' | 'retention_limited'
  billing_complete?: false
  summary?: PerformanceSummary
  items?: T[]
  next_cursor?: string | null
  collection?: {
    mode: 'best_effort'
    counter_period: 'producer_lifetime'
    unobserved_producers_possible: true
    producers: Array<{
      producer_id: string
      user_id: string
      accepted: number
      persisted: number
      dropped: number
      failed: number
      pending: number
      uncertain: number
      clean: number
    }>
  }
}
