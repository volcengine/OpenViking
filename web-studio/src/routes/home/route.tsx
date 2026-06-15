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

export const Route = createFileRoute('/home')({
  component: HomePage,
})

function HomePage() {
  const { t } = useTranslation('home')
  const { connectionRole, isConnectionRoleLoading } = useAppConnection()
  const hasAdminRole = connectionRole === 'admin' || connectionRole === 'root'
  const canQueryAdminMetrics = !isConnectionRoleLoading && hasAdminRole

  const dashboard = useQuery({
    enabled: canQueryAdminMetrics,
    queryFn: fetchConsoleDashboardSummary,
    queryKey: ['console-dashboard-summary'],
    refetchInterval: 30_000,
  })

  const tokenSeries = useQuery({
    enabled: canQueryAdminMetrics,
    queryFn: fetchConsoleTokenSeries,
    queryKey: ['console-token-series', 'last-14-days'],
    refetchInterval: 60_000,
  })

  const contextCommits = useQuery({
    enabled: canQueryAdminMetrics,
    queryFn: fetchConsoleContextCommits,
    queryKey: ['console-context-commits', 'last-365-days'],
    refetchInterval: 60_000,
  })

  const summary = dashboard.data
  const usageDisabled = isDisabledPayload(summary)

  if (!isConnectionRoleLoading && !hasAdminRole) {
    return (
      <div className="flex min-h-[calc(100svh-8rem)] items-center justify-center px-4 py-10">
        <section className="w-full max-w-xl rounded-lg border bg-card px-6 py-5 shadow-sm">
          <h1 className="text-lg font-semibold">{t('limited.title')}</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {t('limited.description')}
          </p>
        </section>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 pb-8">
      <div className="grid gap-4 md:grid-cols-3">
        <ContextDataPanel
          data={summary?.context_counts}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
        <TodayTokensPanel
          data={summary?.today_tokens}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
        <TodayRetrievalsPanel
          data={summary?.today_retrievals}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
      </div>

      <TokenTrendPanel
        data={tokenSeries.data}
        isError={tokenSeries.isError}
        isLoading={tokenSeries.isLoading}
        t={t}
      />

      <ContextCommitsPanel
        data={contextCommits.data}
        isError={contextCommits.isError}
        isLoading={contextCommits.isLoading}
        t={t}
      />
    </div>
  )
}
