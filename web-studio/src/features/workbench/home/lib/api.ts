import {
  getConsoleContextCommits,
  getConsoleDashboardSummary,
  getConsoleTokens,
  getOvResult,
} from '#/shared/api/ov-client/index'

import {
  COMMIT_SERIES_DAYS,
  TOKEN_SERIES_DAYS,
} from '#/features/workbench/home/constants/dashboard'
import {
  getLastDaysRange,
  getViewerTimezone,
} from '#/features/workbench/home/lib/format'
import type {
  ConsoleContextCommitsResult,
  ConsoleDashboardSummaryResult,
  ConsoleSeriesQuery,
  ConsoleTokenSeriesResult,
} from '@ov-server/api/v1/console'

export function fetchConsoleDashboardSummary(): Promise<ConsoleDashboardSummaryResult> {
  return getOvResult<ConsoleDashboardSummaryResult>(
    getConsoleDashboardSummary({ query: { timezone: getViewerTimezone() } }),
  )
}

export function fetchConsoleTokenSeries(): Promise<ConsoleTokenSeriesResult> {
  const tz = getViewerTimezone()
  const range = getLastDaysRange(TOKEN_SERIES_DAYS, tz)
  const query: ConsoleSeriesQuery = {
    bucket: 'day',
    end_date: range.endDate,
    start_date: range.startDate,
    timezone: tz,
  }
  return getOvResult<ConsoleTokenSeriesResult>(getConsoleTokens({ query }))
}

export function fetchConsoleContextCommits(): Promise<ConsoleContextCommitsResult> {
  const tz = getViewerTimezone()
  const range = getLastDaysRange(COMMIT_SERIES_DAYS, tz)
  const query: ConsoleSeriesQuery = {
    bucket: '4h',
    end_date: range.endDate,
    start_date: range.startDate,
    timezone: tz,
  }
  return getOvResult<ConsoleContextCommitsResult>(
    getConsoleContextCommits({ query }),
  )
}
