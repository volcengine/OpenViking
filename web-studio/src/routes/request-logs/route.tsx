import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { ActivityIcon, BarChart3Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MetricCard } from './-components/metric-card'
import { RequestLogPanel } from './-components/panel'
import { DEFAULT_FILTERS, DEFAULT_PAGE_SIZE } from './-constants/audit'
import { fetchAuditLogs, isZeroResultCombination } from './-lib/api'
import { formatPercent } from './-lib/format'
import type { AuditFilters, LogTypeFilter } from './-types/audit'

export const Route = createFileRoute('/request-logs')({
  component: RequestLogsRoute,
})

function RequestLogsRoute() {
  const { t } = useTranslation('requestLogs')
  const [draftFilters, setDraftFilters] =
    React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [filters, setFilters] = React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(DEFAULT_PAGE_SIZE)
  const zeroResult = isZeroResultCombination(filters)

  const audit = useQuery({
    enabled: !zeroResult,
    queryFn: () => fetchAuditLogs(filters, page, pageSize),
    queryKey: ['console-audit-logs', filters, page, pageSize],
    refetchInterval: 30_000,
  })

  const logs = zeroResult ? [] : (audit.data?.items ?? [])
  const disabled = audit.data?.enabled === false
  const total = zeroResult ? 0 : (audit.data?.total ?? 0)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  const handleSearch = () => {
    setFilters({ ...draftFilters })
    setPage(1)
  }

  const handleReset = () => {
    setDraftFilters(DEFAULT_FILTERS)
    setFilters(DEFAULT_FILTERS)
    setPage(1)
  }

  const handleLogTypeChange = (logType: LogTypeFilter) => {
    const nextFilters = { ...draftFilters, logType }
    setDraftFilters(nextFilters)
    setFilters(nextFilters)
    setPage(1)
  }

  const handleRefresh = () => {
    if (zeroResult) return
    void audit.refetch()
  }

  const handlePageSizeChange = (nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(1)
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <div className="grid gap-3 md:grid-cols-2">
        <MetricCard
          label={t('metrics.total')}
          value={total}
          icon={<ActivityIcon className="size-4" />}
        />
        <MetricCard
          label={t('metrics.successRate')}
          value={formatPercent(zeroResult ? 0 : audit.data?.success_rate)}
          icon={<BarChart3Icon className="size-4" />}
        />
      </div>

      <RequestLogPanel
        disabled={disabled}
        disabledMessage={audit.data?.message}
        draftFilters={draftFilters}
        filters={filters}
        isError={audit.isError}
        isFetching={audit.isFetching}
        isLoading={audit.isLoading}
        logs={logs}
        onDraftFiltersChange={setDraftFilters}
        onLogTypeChange={handleLogTypeChange}
        onPageChange={setPage}
        onPageSizeChange={handlePageSizeChange}
        onRefresh={handleRefresh}
        onReset={handleReset}
        onSearch={handleSearch}
        page={page}
        pageCount={pageCount}
        pageSize={pageSize}
        total={total}
        zeroResult={zeroResult}
      />
    </div>
  )
}
