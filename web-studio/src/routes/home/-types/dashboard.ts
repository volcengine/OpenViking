export type HomeT = (key: string, options?: Record<string, unknown>) => string

export type ConsoleDashboardSummary = {
  agent_overview?: AgentOverview
  context_counts?: ContextCounts
  enabled?: boolean
  message?: string
  today_retrievals?: RetrievalCounts
  today_tokens?: TokenCounts
}

export type ContextCounts = {
  files?: number
  memories?: number
  skills?: number
  total?: number
}

export type TokenCounts = {
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

export type RetrievalCounts = {
  find?: number
  search?: number
  total?: number
}

export type AgentOverview = {
  items?: AgentVisit[]
  total?: number
}

export type AgentVisit = {
  agent_id?: string
  last_seen_at?: string
}

export type ConsoleSeries<TItem> = {
  bucket?: string
  enabled?: boolean
  end_date?: string
  items?: TItem[]
  message?: string
  start_date?: string
}

export type TokenSeriesItem = {
  date?: string
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

export type TokenTrendPayload = {
  color?: string
  dataKey?: string
  name?: string
  value?: number
}

export type ContextCommitItem = {
  add_resource?: number
  add_skill?: number
  date?: string
  hour?: number
  session_add_message?: number
  session_commit?: number
  total?: number
}

export type HeatMapDayValue = {
  count: number
  date: string
  details: Required<ContextCommitItem>
}

export type CommitHeatmapStats = {
  activeDays: number
  peakCount: number
  peakDate: string
  recentDate: string
}

export type CommitTooltip = {
  item: HeatMapDayValue
  x: number
  y: number
}
