import {
  AdminScopeFilters,
  useAdminUserScope,
} from '#/features/admin/users/components/admin-scope-filters'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { cn } from '#/shared/lib/utils'

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
  management: boolean,
) {
  const metricsKey = management ? connection.adminApiKey : connection.apiKey
  return {
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: metricsKey ? hashSecret(metricsKey) : 'none',
    keySource: management ? 'admin' : 'data',
    role: connectionRole,
    userId: connection.userId,
  }
}

export function HomePage({
  management = false,
}: { management?: boolean } = {}) {
  const { t } = useTranslation(['home', 'appShell'])
  const { connection, connectionRole, serverMode } = useAppConnection()
  const {
    headers,
    scopeKey,
    isLoading: isConnectionRoleLoading,
    unavailableKey,
    userFilter,
  } = useAdminUserScope(management, 'usage')
  const hasPersonalIdentity =
    connectionRole === 'user' &&
    (serverMode !== 'api_key' || Boolean(connection.apiKey.trim()))
  const hasWorkspaceUsage =
    !management &&
    serverMode === 'api_key' &&
    connectionRole === 'admin' &&
    Boolean(connection.apiKey.trim())
  const canQueryMetrics =
    !isConnectionRoleLoading &&
    connectionRole !== 'unknown' &&
    headers !== null &&
    (management || hasPersonalIdentity || hasWorkspaceUsage)
  const metricsScopeKey = {
    scopeKey,
    identity: getMetricsScopeKey(connection, connectionRole, management),
  }

  const dashboard = useQuery({
    meta: { adminSpaceScope: management ? scopeKey : undefined },
    enabled: canQueryMetrics,
    queryFn: () => fetchConsoleDashboardSummary(headers ?? undefined),
    queryKey: ['console-dashboard-summary', metricsScopeKey, serverMode],
    refetchInterval: 30_000,
  })

  const tokenSeries = useQuery({
    meta: { adminSpaceScope: management ? scopeKey : undefined },
    enabled: canQueryMetrics,
    queryFn: () => fetchConsoleTokenSeries(headers ?? undefined),
    queryKey: [
      'console-token-series',
      'last-14-days',
      metricsScopeKey,
      serverMode,
    ],
    refetchInterval: 60_000,
  })

  const contextCommits = useQuery({
    meta: { adminSpaceScope: management ? scopeKey : undefined },
    enabled: canQueryMetrics,
    queryFn: () => fetchConsoleContextCommits(headers ?? undefined),
    queryKey: [
      'console-context-commits',
      'last-365-days',
      metricsScopeKey,
      serverMode,
    ],
    refetchInterval: 60_000,
  })

  const summary = dashboard.data
  const identityUnavailable = !isConnectionRoleLoading && !canQueryMetrics
  const hasManagementCredential =
    serverMode === 'api_key' &&
    (connectionRole === 'admin' || connectionRole === 'root')
  const metricsUnavailable = identityUnavailable || isDisabledPayload(summary)
  const unavailableMessage = identityUnavailable
    ? headers === null
      ? t(unavailableKey)
      : t(management ? 'usageAccessRequired' : 'personalUsageAccessRequired')
    : t('usageDisabled')
  const isMetricsLoading =
    isConnectionRoleLoading || (canQueryMetrics && dashboard.isPending)
  const isSeriesLoading =
    isConnectionRoleLoading || (canQueryMetrics && tokenSeries.isPending)
  const isCommitsLoading =
    isConnectionRoleLoading || (canQueryMetrics && contextCommits.isPending)

  return (
    <div
      className={cn(
        'flex min-w-0 flex-col gap-5 pb-8',
        !management &&
          '[--home-token-input:#ed2887] [--home-token-output:#18b968] [--home-token-embedding:#3079ff] [&_[data-slot=usage-panel]]:rounded-[20px] [&_[data-slot=usage-panel]]:border-transparent [&_[data-slot=usage-panel]]:bg-[#f7f7f9] dark:[&_[data-slot=usage-panel]]:bg-[#262628]',
      )}
    >
      {management && <AdminScopeFilters userFilter={userFilter} />}
      {!management ? (
        <h1 className="text-xl font-semibold">
          {t(hasWorkspaceUsage ? 'workspaceUsage' : 'myUsage')}
        </h1>
      ) : null}
      {!management && identityUnavailable ? (
        <div className="rounded-[20px] bg-muted/50 p-6 text-sm text-muted-foreground">
          <p>
            {hasManagementCredential
              ? t('personalUsageManagementScope', { userId: connection.userId })
              : unavailableMessage}
          </p>
          {hasManagementCredential ? (
            <Link
              to="/admin/dashboard"
              className="mt-3 inline-block font-medium text-primary hover:underline"
            >
              {t('viewWorkspaceUsage')}
            </Link>
          ) : null}
        </div>
      ) : (
        <>
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
            showTotal={!management}
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
        </>
      )}
    </div>
  )
}
