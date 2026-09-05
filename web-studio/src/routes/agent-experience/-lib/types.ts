export type ExperienceFileItem = {
  /** File name, e.g. `exchange.md`. */
  name: string
  /** Full Viking URI, e.g. `viking://user/default/memories/experiences/exchange.md`. */
  uri: string
  /** Last modification time reported by `GET /api/v1/fs/ls` (`modTime`). */
  modTime?: string
  /** File size in bytes. */
  size?: number
}

export type ExperiencePage = {
  items: ExperienceFileItem[]
  total: number
  page: number
  pageSize: number
}

export type TrajectoryOutcome =
  'success' | 'failure' | 'partial' | 'unknown' | 'unfinished'

export type OutcomeCount = {
  outcome: TrajectoryOutcome
  count: number
}

export type TrajectoryItem = {
  uri: string
  name: string
  description?: string
  created_at?: string
  updated_at?: string
}

export type TrajectoryPage = {
  experienceUri: string
  items: TrajectoryItem[]
  total: number
  limit: number
  offset: number
  hasMore: boolean
}

export type OutcomeDistribution = {
  experienceUri: string
  distribution: OutcomeCount[]
}

/** Quick time-range options shared by the outcome and trajectory queries. */
export type TimeRangePreset = 'all' | '7d' | '30d' | 'custom'

export type TimeRange = {
  preset: TimeRangePreset
  /** UTC `YYYY-MM-DD` inclusive lower bound, or `undefined` for no filter. */
  startDate?: string
  /** UTC `YYYY-MM-DD` inclusive upper bound, or `undefined` for no filter. */
  endDate?: string
}

export type AgentEvolutionStatus = {
  enabled: boolean
  accountId?: string
}
