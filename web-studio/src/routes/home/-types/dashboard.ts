import type { ConsoleContextCommitItem } from '@ov-server/api/v1/console'

export type HomeT = (key: string, options?: Record<string, unknown>) => string

export type {
  ConsoleContextCommitItem as ContextCommitItem,
  ConsoleContextCounts as ContextCounts,
  ConsoleDashboardSummaryResult as ConsoleDashboardSummary,
  ConsoleRetrievalCounts as RetrievalCounts,
  ConsoleSeriesResult as ConsoleSeries,
  ConsoleTokenCounts as TokenCounts,
  ConsoleTokenSeriesItem as TokenSeriesItem,
} from '@ov-server/api/v1/console'

export type TokenTrendPayload = {
  color?: string
  dataKey?: string
  name?: string
  value?: number
}

export type HeatMapDayValue = {
  count: number
  date: string
  details: Required<ConsoleContextCommitItem>
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
