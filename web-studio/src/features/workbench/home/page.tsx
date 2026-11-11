import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/features/connection/lib/connection-runtime'
import { ContextCommitsPanel } from '#/features/workbench/home/components/context-commits-panel'
import {
  ContextDataPanel,
  TodayRetrievalsPanel,
  TodayTokensPanel,
} from '#/features/workbench/home/components/metric-panels'
import { TokenTrendPanel } from '#/features/workbench/home/components/token-trend-panel'
import {
  fetchConsoleContextCommits,
  fetchConsoleDashboardSummary,
  fetchConsoleTokenSeries,
} from '#/features/workbench/home/lib/api'
import { isDisabledPayload } from '#/features/workbench/home/lib/format'

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
  const metricsKey = connection.apiKey
  return {
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: metricsKey ? hashSecret(metricsKey) : 'none',
    keySource: connection.apiKey ? 'api' : 'none',
    role: connectionRole,
    userId: connection.userId,
  }
}

export function HomePage() {
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
  const identityUnavailable =
    !isConnectionRoleLoading && connectionRole === 'unknown'
  const metricsUnavailable = identityUnavailable || isDisabledPayload(summary)
  const unavailableMessage = identityUnavailable
    ? t('usageAccessRequired')
    : t('usageDisabled')
  const isMetricsLoading =
    isConnectionRoleLoading || (canQueryMetrics && dashboard.isPending)
  const isSeriesLoading =
    isConnectionRoleLoading || (canQueryMetrics && tokenSeries.isPending)
  const isCommitsLoading =
    isConnectionRoleLoading || (canQueryMetrics && contextCommits.isPending)

  return (
    <div className="flex flex-col gap-5 pb-8">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,18rem),1fr))] gap-4">
        <ContextDataPanel
          data={summary?.context_counts}
          disabled={metricsUnavailable}
          disabledMessage={unavailableMessage}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
        <TodayTokensPanel
          data={summary?.today_tokens}
          disabled={metricsUnavailable}
          disabledMessage={unavailableMessage}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
        <TodayRetrievalsPanel
          data={summary?.today_retrievals}
          disabled={metricsUnavailable}
          disabledMessage={unavailableMessage}
          isError={dashboard.isError}
          isLoading={isMetricsLoading}
          t={t}
        />
      </div>

      <TokenTrendPanel
        data={tokenSeries.data}
        disabled={identityUnavailable}
        disabledMessage={unavailableMessage}
        isError={tokenSeries.isError}
        isLoading={isSeriesLoading}
        t={t}
      />

      <ContextCommitsPanel
        data={contextCommits.data}
        disabled={identityUnavailable}
        disabledMessage={unavailableMessage}
        isError={contextCommits.isError}
        isLoading={isCommitsLoading}
        t={t}
      />
    </div>
  )
}
