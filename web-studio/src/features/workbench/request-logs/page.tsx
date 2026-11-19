import styles from './page.module.css'
import {
  AdminScopeFilters,
  useAdminUserScope,
} from '#/features/admin/users/components/admin-scope-filters'
import { useQuery } from '@tanstack/react-query'
import * as React from 'react'

import { ActivityIcon, BarChart3Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { EmptyLogsState } from '#/features/workbench/request-logs/components/empty-logs-state'
import { MetricCard } from '#/features/workbench/request-logs/components/metric-card'
import { RequestLogPanel } from '#/features/workbench/request-logs/components/panel'
import {
  DEFAULT_FILTERS,
  DEFAULT_PAGE_SIZE,
} from '#/features/workbench/request-logs/constants/audit'
import {
  fetchAuditLogs,
  isZeroResultCombination,
} from '#/features/workbench/request-logs/lib/api'
import { formatPercent } from '#/features/workbench/request-logs/lib/format'
import type {
  AuditFilters,
  LogTypeFilter,
} from '#/features/workbench/request-logs/types/audit'

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

export function RequestLogsRoute({
  management = false,
}: { management?: boolean } = {}) {
  const { t } = useTranslation(['requestLogs', 'appShell'])
  const { connection, connectionRole, serverMode } = useAppConnection()
  const {
    headers,
    scopeKey,
    isLoading: isConnectionRoleLoading,
    unavailableKey,
    userFilter,
  } = useAdminUserScope(management, 'usage')
  const [draftFilters, setDraftFilters] =
    React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [filters, setFilters] = React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(DEFAULT_PAGE_SIZE)
  const zeroResult = isZeroResultCombination(filters)
  const canQueryAudit =
    !isConnectionRoleLoading && connectionRole !== 'unknown' && headers !== null
  const auditScopeKey = {
    scopeKey,
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: hashSecret(
      management ? connection.adminApiKey : connection.apiKey,
    ),
    management,
    serverMode,
    role: connectionRole,
    userId: connection.userId,
  }

  const audit = useQuery({
    meta: { adminSpaceScope: management ? scopeKey : undefined },
    enabled: canQueryAudit && !zeroResult,
    queryFn: () =>
      fetchAuditLogs(filters, page, pageSize, headers ?? undefined),
    queryKey: ['console-audit-logs', auditScopeKey, filters, page, pageSize],
    refetchInterval: 30_000,
  })

  React.useEffect(() => {
    setPage(1)
  }, [scopeKey])

  const logs = !canQueryAudit || zeroResult ? [] : (audit.data?.items ?? [])
  const disabled = audit.data?.enabled === false
  const hasMetrics =
    zeroResult || (!isConnectionRoleLoading && audit.isSuccess && !disabled)
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

  return (
    <div
      className={
        management ? styles.page : 'flex w-full min-w-0 flex-col gap-5'
      }
    >
      {management ? (
        <>
          <h1 className="text-xl font-semibold tracking-tight">
            {t('appShell:navigation.requestLogs.title')}
          </h1>
          <AdminScopeFilters userFilter={userFilter} />
        </>
      ) : null}
      {!management ? (
        <div className="rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          {scopeLabel}
        </div>
      ) : null}

      {!isConnectionRoleLoading && !canQueryAudit ? (
        <EmptyLogsState
          title={t('accessRequired.title')}
          description={
            headers === null
              ? t(unavailableKey)
              : t('accessRequired.description')
          }
        />
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2" data-log-metrics>
            <MetricCard
              label={t('metrics.total')}
              value={hasMetrics ? (total >= 1000 ? '999+' : total) : '—'}
              icon={<ActivityIcon className="size-4" />}
            />
            <MetricCard
              label={t('metrics.successRate')}
              value={
                hasMetrics
                  ? formatPercent(zeroResult ? 0 : audit.data?.success_rate)
                  : '—'
              }
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
            isLoading={
              isConnectionRoleLoading || (!zeroResult && audit.isPending)
            }
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
        </>
      )}
    </div>
  )
}
