import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'

import { ContextCommitsPanel } from './-components/context-commits-panel'
import {
  ContextDataPanel,
  TodayRetrievalsPanel,
  TodayTokensPanel,
} from './-components/metric-panels'
import { TokenTrendPanel } from './-components/token-trend-panel'
import {
  fetchConsoleContextCommits,
  fetchConsoleDashboardSummary,
  fetchConsoleTokenSeries,
} from './-lib/api'
import { isDisabledPayload } from './-lib/format'
import { useAppConnection } from '#/hooks/use-app-connection'
import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/hooks/use-app-connection'

export const Route = createFileRoute('/home')({
  component: HomePage,
})

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

function getMetricsScopeKey(
  connection: ConnectionDraft,
  connectionRole: ConnectionRole,
) {
  const metricsKey = connection.apiKey || connection.adminApiKey
  return {
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: metricsKey ? hashSecret(metricsKey) : 'none',
    keySource: connection.apiKey
      ? 'api'
      : connection.adminApiKey
        ? 'admin'
        : 'none',
    role: connectionRole,
    userId: connection.userId,
  }
}

function HomePage() {
  const { t } = useTranslation('home')
  const { connection, connectionRole, isConnectionRoleLoading } =
    useAppConnection()
  const canQueryMetrics =
    !isConnectionRoleLoading && connectionRole !== 'unknown'
  const metricsScopeKey = getMetricsScopeKey(connection, connectionRole)

  const dashboard = useQuery({
    enabled: canQueryMetrics,
    queryFn: fetchConsoleDashboardSummary,
    queryKey: ['console-dashboard-summary', metricsScopeKey],
    refetchInterval: 30_000,
  })

  const tokenSeries = useQuery({
    enabled: canQueryMetrics,
    queryFn: fetchConsoleTokenSeries,
    queryKey: ['console-token-series', 'last-14-days', metricsScopeKey],
    refetchInterval: 60_000,
  })

  const contextCommits = useQuery({
    enabled: canQueryMetrics,
    queryFn: fetchConsoleContextCommits,
    queryKey: ['console-context-commits', 'last-365-days', metricsScopeKey],
    refetchInterval: 60_000,
  })

  const summary = dashboard.data
  const metricsUnavailable =
    (!isConnectionRoleLoading && connectionRole === 'unknown') ||
    isDisabledPayload(summary)
  const isMetricsLoading = isConnectionRoleLoading || dashboard.isLoading
  const isSeriesLoading = isConnectionRoleLoading || tokenSeries.isLoading
  const isCommitsLoading = isConnectionRoleLoading || contextCommits.isLoading

  return (
    <div className="flex flex-col gap-5 pb-8">
      <div className="grid gap-4 md:grid-cols-3">
        <ContextDataPanel
          data={summary?.context_counts}
          disabled={metricsUnavailable}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
        <TodayTokensPanel
          data={summary?.today_tokens}
          disabled={metricsUnavailable}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
        <TodayRetrievalsPanel
          data={summary?.today_retrievals}
          disabled={metricsUnavailable}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
      </div>

      <TokenTrendPanel
        data={tokenSeries.data}
        disabled={metricsUnavailable}
        isError={tokenSeries.isError}
        isLoading={isSeriesLoading}
        t={t}
      />

      <ContextCommitsPanel
        data={contextCommits.data}
        disabled={metricsUnavailable}
        isError={contextCommits.isError}
        isLoading={isCommitsLoading}
        t={t}
      />
    </div>
  )
}
