import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Navigate, createFileRoute } from '@tanstack/react-router'
import { ActivityIcon, BarChart3Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MetricCard } from './-components/metric-card'
import { RequestLogPanel } from './-components/panel'
import { DEFAULT_FILTERS, DEFAULT_PAGE_SIZE } from './-constants/audit'
import { fetchAuditLogs, isZeroResultCombination } from './-lib/api'
import { formatPercent } from './-lib/format'
import type { AuditFilters, LogTypeFilter } from './-types/audit'
import { useAppConnection } from '#/hooks/use-app-connection'

export const Route = createFileRoute('/request-logs')({
  component: RequestLogsRoute,
})

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

function RequestLogsRoute() {
  const { t } = useTranslation('requestLogs')
  const { connection, connectionRole, isConnectionRoleLoading } =
    useAppConnection()
  const [draftFilters, setDraftFilters] =
    React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [filters, setFilters] = React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(DEFAULT_PAGE_SIZE)
  const zeroResult = isZeroResultCombination(filters)
  const canQueryAudit = !isConnectionRoleLoading && connectionRole !== 'unknown'
  const auditScopeKey = {
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: hashSecret(connection.adminApiKey || connection.apiKey),
    role: connectionRole,
    userId: connection.userId,
  }

  const audit = useQuery({
    enabled: canQueryAudit && !zeroResult,
    queryFn: () => fetchAuditLogs(filters, page, pageSize),
    queryKey: ['console-audit-logs', auditScopeKey, filters, page, pageSize],
    refetchInterval: 30_000,
  })

  const logs = zeroResult ? [] : (audit.data?.items ?? [])
  const disabled = audit.data?.enabled === false
  const total = zeroResult ? 0 : (audit.data?.total ?? 0)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const identityParts = [connection.accountId, connection.userId].filter(
    Boolean,
  )
  const scopeLabel =
    identityParts.length > 0
      ? t('scope.currentIdentityWithName', {
          identity: identityParts.join(' / '),
        })
      : t('scope.currentIdentity')

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

  if (!isConnectionRoleLoading && !canQueryAudit) {
    return <Navigate replace to="/home" />
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <div className="rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
        {scopeLabel}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <MetricCard
          label={t('metrics.total')}
          value={total >= 1000 ? '999+' : total}
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
