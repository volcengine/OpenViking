import {
  getConsoleContextCommits,
  getConsoleDashboardSummary,
  getConsoleTokens,
  getOvResult,
} from '#/lib/ov-client'

import { COMMIT_SERIES_DAYS, TOKEN_SERIES_DAYS } from '../-constants/dashboard'
import type {
  ConsoleSeriesQuery,
  ConsoleContextCommitsResult,
  ConsoleDashboardSummaryResult,
  ConsoleTokenSeriesResult,
} from '@ov-server/api/v1/console'
import { getLastDaysRange, getViewerTimezone } from './format'

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
