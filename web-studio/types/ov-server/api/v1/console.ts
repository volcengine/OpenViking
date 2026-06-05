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

export type ConsoleTokenSeriesResult = ConsoleSeriesResult<ConsoleTokenSeriesItem>

export type ConsoleContextCommitItem = {
  add_resource?: number
  add_skill?: number
  date?: string
  hour?: number
  session_add_message?: number
  session_commit?: number
  total?: number
}

export type ConsoleContextCommitsResult = ConsoleSeriesResult<ConsoleContextCommitItem>

export type ConsoleAuditLogItem = {
  account_id?: string | null
  api_type?: string
  created_at?: string
  duration_ms?: number
  method?: string
  request_id?: string | null
  route?: string
  status_code?: number
  user_id?: string | null
}

export type ConsoleAuditResult = OvMaybeDisabled & PaginatedResult<ConsoleAuditLogItem> & {
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
