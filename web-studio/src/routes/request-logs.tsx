import * as React from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ActivityIcon, BarChart3Icon, RefreshCwIcon, RotateCcwIcon, SearchIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '#/components/ui/pagination'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { client } from '#/gen/ov-client/client.gen'
import { getOvResult } from '#/lib/ov-client'
import { cn } from '#/lib/utils'

export const Route = createFileRoute('/request-logs')({
  component: RequestLogsRoute,
})

type LogTypeFilter = 'all' | 'error'

type AuditLogItem = {
  account_id?: string | null
  agent_id?: string | null
  api_type?: string
  created_at?: string
  duration_ms?: number
  method?: string
  request_id?: string | null
  route?: string
  status_code?: number
  user_id?: string | null
}

type AuditLogResponse = {
  enabled?: boolean
  items?: AuditLogItem[]
  message?: string
  page?: number
  page_size?: number
  success_rate?: number
  total?: number
}

type RequestLogStatus = 'success' | 'error'

type AuditFilters = {
  apiType: string
  logType: LogTypeFilter
  requestId: string
  statusCode: string
}

const DEFAULT_FILTERS: AuditFilters = {
  apiType: '',
  logType: 'all',
  requestId: '',
  statusCode: '',
}

const LOG_TYPE_FILTERS: LogTypeFilter[] = ['all', 'error']
const PAGE_SIZE = 10

function buildAuditQuery(filters: AuditFilters, page: number): Record<string, string | number> {
  const query: Record<string, string | number> = {
    page,
    page_size: PAGE_SIZE,
  }

  const requestId = filters.requestId.trim()
  const statusCode = filters.statusCode.trim()
  const apiType = filters.apiType.trim()

  if (requestId) {
    query.request_id = requestId
  }

  if (apiType) {
    query.api_type = apiType
  }

  if (statusCode) {
    query.status = statusCode
  } else if (filters.logType === 'error') {
    query.status = 'error'
  }

  return query
}

function isZeroResultCombination(filters: AuditFilters): boolean {
  if (filters.logType !== 'error') return false
  const rawStatusCode = filters.statusCode.trim()
  if (!rawStatusCode) return false
  const statusCode = Number(rawStatusCode)
  return Number.isFinite(statusCode) && statusCode < 400
}

function fetchAuditLogs(filters: AuditFilters, page: number): Promise<AuditLogResponse> {
  return getOvResult<AuditLogResponse>(
    client.get({
      query: buildAuditQuery(filters, page),
      url: '/api/v1/console/audit',
    }),
  )
}

function normalizeStatus(statusCode?: number): RequestLogStatus {
  return statusCode !== undefined && statusCode >= 200 && statusCode < 400 ? 'success' : 'error'
}

function formatTime(value?: string): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

function formatDuration(value?: number): string {
  if (value === undefined) {
    return '-'
  }

  if (value < 1000) {
    return `${Math.round(value)} ms`
  }

  return `${(value / 1000).toFixed(2)} s`
}

function formatPercent(value?: number): string {
  if (value === undefined) return '-'
  return `${Math.round(value * 100)}%`
}

function getStatusTone(status: RequestLogStatus, statusCode?: number): string {
  if (status === 'error' || (statusCode && statusCode >= 400)) {
    return 'border-destructive/20 bg-destructive/10 text-destructive'
  }

  return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
}

function methodTone(method: string): string {
  switch (method) {
    case 'GET':
      return 'text-sky-700 dark:text-sky-300'
    case 'POST':
      return 'text-emerald-700 dark:text-emerald-300'
    case 'PUT':
    case 'PATCH':
      return 'text-amber-700 dark:text-amber-300'
    case 'DELETE':
      return 'text-destructive'
    default:
      return 'text-muted-foreground'
  }
}

function RequestLogsRoute() {
  const { t } = useTranslation('requestLogs')
  const [draftFilters, setDraftFilters] = React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [filters, setFilters] = React.useState<AuditFilters>(DEFAULT_FILTERS)
  const [page, setPage] = React.useState(1)
  const zeroResult = isZeroResultCombination(filters)

  const audit = useQuery({
    enabled: !zeroResult,
    queryFn: () => fetchAuditLogs(filters, page),
    queryKey: ['console-audit-logs', filters, page],
    refetchInterval: 30_000,
  })

  const logs = zeroResult ? [] : audit.data?.items ?? []
  const disabled = audit.data?.enabled === false
  const total = zeroResult ? 0 : audit.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const handleSearch = () => {
    setFilters({ ...draftFilters })
    setPage(1)
  }

  const handleReset = () => {
    setDraftFilters(DEFAULT_FILTERS)
    setFilters(DEFAULT_FILTERS)
    setPage(1)
  }

  const handleRefresh = () => {
    if (zeroResult) return
    audit.refetch()
  }

  return (
    <div className='flex w-full min-w-0 flex-col gap-5'>
      <div className='grid gap-3 md:grid-cols-2'>
        <MetricCard label={t('metrics.total')} value={total} icon={<ActivityIcon className='size-4' />} />
        <MetricCard label={t('metrics.successRate')} value={formatPercent(zeroResult ? 0 : audit.data?.success_rate)} icon={<BarChart3Icon className='size-4' />} />
      </div>

      <Card className='overflow-hidden'>
        <CardHeader className='gap-4 border-b bg-muted/20'>
          <div className='grid min-w-0 grid-cols-[9rem_minmax(0,1fr)] items-center gap-3'>
            <CardTitle className='text-base leading-tight whitespace-nowrap'>{t('table.title')}</CardTitle>
            <form
              className='grid min-w-0 grid-cols-[minmax(12rem,1fr)_6.5rem_9rem_auto_auto_auto_auto] items-center gap-2'
              onSubmit={(event) => {
                event.preventDefault()
                handleSearch()
              }}
            >
              <div className='relative min-w-0'>
                <SearchIcon className='pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
                <Input
                  value={draftFilters.requestId}
                  onChange={(event) => setDraftFilters((current) => ({ ...current, requestId: event.target.value }))}
                  placeholder={t('filters.requestIdPlaceholder')}
                  className='pl-8'
                />
              </div>
              <Input
                value={draftFilters.statusCode}
                onChange={(event) => setDraftFilters((current) => ({ ...current, statusCode: event.target.value.replace(/\D/g, '').slice(0, 3) }))}
                placeholder={t('filters.statusCodePlaceholder')}
                className='w-full'
                inputMode='numeric'
              />
              <Input
                value={draftFilters.apiType}
                onChange={(event) => setDraftFilters((current) => ({ ...current, apiType: event.target.value }))}
                placeholder={t('filters.apiTypePlaceholder')}
                className='w-full'
              />
              <div className='flex rounded-md border bg-background p-0.5'>
                {LOG_TYPE_FILTERS.map((item) => (
                  <button
                    key={item}
                    type='button'
                    onClick={() => {
                      const nextFilters = { ...draftFilters, logType: item }
                      setDraftFilters(nextFilters)
                      setFilters(nextFilters)
                      setPage(1)
                    }}
                    className={cn(
                      'h-8 whitespace-nowrap rounded-sm px-3 text-sm text-muted-foreground transition-colors hover:text-foreground',
                      filters.logType === item && 'bg-muted text-foreground shadow-xs',
                    )}
                  >
                    {t(`filters.${item}`)}
                  </button>
                ))}
              </div>
              <Button type='submit' disabled={audit.isFetching}>
                <SearchIcon />
                {t('query')}
              </Button>
              <Button type='button' variant='outline' onClick={handleReset} disabled={audit.isFetching}>
                <RotateCcwIcon />
                {t('reset')}
              </Button>
              <Button type='button' variant='outline' onClick={handleRefresh} disabled={audit.isFetching || zeroResult}>
                <RefreshCwIcon className={cn(audit.isFetching && 'animate-spin')} />
                {t('refresh')}
              </Button>
            </form>
          </div>
        </CardHeader>
        <CardContent className='p-0'>
          {audit.isLoading && !zeroResult ? (
            <div className='flex min-h-72 items-center justify-center text-sm text-muted-foreground'>
              {t('loading')}
            </div>
          ) : audit.isError ? (
            <EmptyLogsState title={t('error.title')} description={t('error.description')} />
          ) : disabled ? (
            <EmptyLogsState title={t('disabled.title')} description={audit.data?.message || t('disabled.description')} />
          ) : logs.length === 0 ? (
            <div className='flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center'>
              <div className='flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground'>
                <ActivityIcon className='size-5' />
              </div>
              <div>
                <p className='font-medium'>{t('empty.title')}</p>
                <p className='mt-1 text-sm text-muted-foreground'>{t('empty.description')}</p>
              </div>
            </div>
          ) : (
            <>
              <div className='overflow-x-auto'>
                <Table>
                  <TableHeader>
                    <TableRow className='bg-muted/20 hover:bg-muted/20'>
                      <TableHead>{t('table.time')}</TableHead>
                      <TableHead>{t('table.apiType')}</TableHead>
                      <TableHead>{t('table.method')}</TableHead>
                      <TableHead>{t('table.path')}</TableHead>
                      <TableHead>{t('table.status')}</TableHead>
                      <TableHead className='text-right'>{t('table.duration')}</TableHead>
                      <TableHead>{t('table.requestId')}</TableHead>
                      <TableHead>{t('table.accountId')}</TableHead>
                      <TableHead>{t('table.userId')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {logs.map((log, index) => (
                      <RequestLogRow
                        key={`${log.request_id ?? 'request'}-${log.created_at ?? index}`}
                        log={log}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
              <RequestLogPagination
                page={page}
                pageCount={pageCount}
                total={total}
                onPageChange={setPage}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function EmptyLogsState({ description, title }: { description: string; title: string }) {
  return (
    <div className='flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center'>
      <div className='flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground'>
        <ActivityIcon className='size-5' />
      </div>
      <div>
        <p className='font-medium'>{title}</p>
        <p className='mt-1 text-sm text-muted-foreground'>{description}</p>
      </div>
    </div>
  )
}

function MetricCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <Card className='bg-card/70'>
      <CardContent className='flex items-center justify-between gap-4 p-4'>
        <div>
          <p className='text-sm text-muted-foreground'>{label}</p>
          <p className='mt-1 text-2xl font-semibold tabular-nums'>{value}</p>
        </div>
        <div className='flex size-9 items-center justify-center rounded-md border bg-background/70 text-muted-foreground'>
          {icon}
        </div>
      </CardContent>
    </Card>
  )
}

function RequestLogRow({ log }: { log: AuditLogItem }) {
  const { t } = useTranslation('requestLogs')
  const status = normalizeStatus(log.status_code)
  const method = log.method ?? '-'
  const isSlow = (log.duration_ms ?? 0) > 1000

  return (
    <TableRow>
      <TableCell className='text-muted-foreground tabular-nums'>{formatTime(log.created_at)}</TableCell>
      <TableCell className='max-w-40 truncate font-mono text-xs text-muted-foreground'>{log.api_type || '-'}</TableCell>
      <TableCell>
        <span className={cn('font-mono text-xs font-semibold', methodTone(method))}>{method}</span>
      </TableCell>
      <TableCell className='max-w-[34rem]'>
        <div className='truncate font-mono text-xs text-foreground'>{log.route || '/'}</div>
      </TableCell>
      <TableCell>
        <Badge variant='outline' className={cn('font-mono text-xs', getStatusTone(status, log.status_code))}>
          {log.status_code ?? t(`status.${status}`)}
        </Badge>
      </TableCell>
      <TableCell className={cn('text-right font-mono text-xs tabular-nums text-muted-foreground', isSlow && 'font-semibold text-amber-600 dark:text-amber-300')}>
        {formatDuration(log.duration_ms)}
      </TableCell>
      <TableCell className='max-w-44 truncate font-mono text-xs text-muted-foreground'>
        {log.request_id || '-'}
      </TableCell>
      <TableCell className='max-w-36 truncate font-mono text-xs text-muted-foreground'>
        {log.account_id || '-'}
      </TableCell>
      <TableCell className='max-w-36 truncate font-mono text-xs text-muted-foreground'>
        {log.user_id || '-'}
      </TableCell>
    </TableRow>
  )
}

function RequestLogPagination({
  onPageChange,
  page,
  pageCount,
  total,
}: {
  onPageChange: (page: number) => void
  page: number
  pageCount: number
  total: number
}) {
  const { t } = useTranslation('requestLogs')
  const pages = React.useMemo(() => {
    const start = Math.max(1, page - 2)
    const end = Math.min(pageCount, start + 4)
    return Array.from({ length: end - start + 1 }, (_, index) => start + index)
  }, [page, pageCount])

  return (
    <div className='flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between'>
      <p className='text-sm text-muted-foreground'>
        {t('pagination.summary', { page, pageCount, total })}
      </p>
      <Pagination className='mx-0 w-auto justify-start sm:justify-end'>
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href='#'
              text={t('pagination.previous')}
              aria-disabled={page <= 1}
              className={cn(page <= 1 && 'pointer-events-none opacity-50')}
              onClick={(event) => {
                event.preventDefault()
                if (page > 1) onPageChange(page - 1)
              }}
            />
          </PaginationItem>
          {pages.map((item) => (
            <PaginationItem key={item}>
              <PaginationLink
                href='#'
                isActive={item === page}
                onClick={(event) => {
                  event.preventDefault()
                  onPageChange(item)
                }}
              >
                {item}
              </PaginationLink>
            </PaginationItem>
          ))}
          <PaginationItem>
            <PaginationNext
              href='#'
              text={t('pagination.next')}
              aria-disabled={page >= pageCount}
              className={cn(page >= pageCount && 'pointer-events-none opacity-50')}
              onClick={(event) => {
                event.preventDefault()
                if (page < pageCount) onPageChange(page + 1)
              }}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  )
}
