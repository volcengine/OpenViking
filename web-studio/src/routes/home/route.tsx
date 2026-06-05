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

export const Route = createFileRoute('/home')({
  component: HomePage,
})

function HomePage() {
  const { t } = useTranslation('home')

  const dashboard = useQuery({
    queryFn: fetchConsoleDashboardSummary,
    queryKey: ['console-dashboard-summary'],
    refetchInterval: 30_000,
  })

  const tokenSeries = useQuery({
    queryFn: fetchConsoleTokenSeries,
    queryKey: ['console-token-series', 'last-14-days'],
    refetchInterval: 60_000,
  })

  const contextCommits = useQuery({
    queryFn: fetchConsoleContextCommits,
    queryKey: ['console-context-commits', 'last-365-days'],
    refetchInterval: 60_000,
  })

  const summary = dashboard.data
  const usageDisabled = isDisabledPayload(summary)

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
