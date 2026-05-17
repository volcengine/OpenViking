import { client } from '#/gen/ov-client/client.gen'
import { getOvResult } from '#/lib/ov-client'

import {
  COMMIT_SERIES_DAYS,
  TOKEN_SERIES_DAYS,
} from '../-constants/dashboard'
import type {
  ConsoleDashboardSummary,
  ConsoleSeries,
  ContextCommitItem,
  TokenSeriesItem,
} from '../-types/dashboard'
import { getLastDaysRange } from './format'

export function fetchConsoleDashboardSummary(): Promise<ConsoleDashboardSummary> {
  return getOvResult<ConsoleDashboardSummary>(
    client.get({ url: '/api/v1/console/dashboard/summary' }),
  )
}

export function fetchConsoleTokenSeries(): Promise<ConsoleSeries<TokenSeriesItem>> {
  const range = getLastDaysRange(TOKEN_SERIES_DAYS)
  return getOvResult<ConsoleSeries<TokenSeriesItem>>(
    client.get({
      query: {
        bucket: 'day',
        end_date: range.endDate,
        start_date: range.startDate,
      },
      url: '/api/v1/console/tokens',
    }),
  )
}

export function fetchConsoleContextCommits(): Promise<ConsoleSeries<ContextCommitItem>> {
  const range = getLastDaysRange(COMMIT_SERIES_DAYS)
  return getOvResult<ConsoleSeries<ContextCommitItem>>(
    client.get({
      query: {
        bucket: '4h',
        end_date: range.endDate,
        start_date: range.startDate,
      },
      url: '/api/v1/console/context-commits',
    }),
  )
}
