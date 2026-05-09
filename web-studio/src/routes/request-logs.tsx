import * as React from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { ActivityIcon, Clock3Icon, EraserIcon, RefreshCwIcon, SearchIcon, ServerCrashIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { clearRequestLogs, getRequestLogSnapshot, subscribeRequestLogs } from '#/lib/request-logs'
import type { RequestLogEntry, RequestLogStatus } from '#/lib/request-logs'
import { cn } from '#/lib/utils'

export const Route = createFileRoute('/request-logs')({
  component: RequestLogsRoute,
})

type StatusFilter = 'all' | RequestLogStatus

const STATUS_FILTERS: StatusFilter[] = ['all', 'pending', 'success', 'error']

function useRequestLogs() {
  return React.useSyncExternalStore(subscribeRequestLogs, getRequestLogSnapshot, getRequestLogSnapshot)
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatDuration(value?: number): string {
  if (value === undefined) {
    return '-'
  }

  if (value < 1000) {
    return `${value} ms`
  }

  return `${(value / 1000).toFixed(2)} s`
}

function getStatusTone(status: RequestLogStatus, statusCode?: number): string {
  if (status === 'pending') {
    return 'border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }

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
  const logs = useRequestLogs()
  const [query, setQuery] = React.useState('')
  const [status, setStatus] = React.useState<StatusFilter>('all')

  const filteredLogs = React.useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()

    return logs.filter((log) => {
      const matchesStatus = status === 'all' || log.status === status
      const matchesQuery = !normalizedQuery
        || log.path.toLowerCase().includes(normalizedQuery)
        || log.method.toLowerCase().includes(normalizedQuery)
        || String(log.statusCode ?? '').includes(normalizedQuery)

      return matchesStatus && matchesQuery
    })
  }, [logs, query, status])

  const totals = React.useMemo(() => {
    return logs.reduce(
      (acc, log) => {
        acc.total += 1
        acc[log.status] += 1
        if (log.durationMs !== undefined) {
          acc.durationCount += 1
          acc.durationTotal += log.durationMs
        }
        return acc
      },
      { durationCount: 0, durationTotal: 0, error: 0, pending: 0, success: 0, total: 0 },
    )
  }, [logs])

  const averageDuration = totals.durationCount > 0
    ? Math.round(totals.durationTotal / totals.durationCount)
    : undefined

  return (
    <div className='mx-auto flex w-full max-w-7xl flex-col gap-5'>
      <div className='flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between'>
        <div className='space-y-2'>
          <div className='flex items-center gap-2 text-sm font-medium text-muted-foreground'>
            <ActivityIcon className='size-4' />
            <span>{t('eyebrow')}</span>
          </div>
          <div>
            <h1 className='text-2xl font-semibold tracking-tight md:text-3xl'>{t('title')}</h1>
            <p className='mt-2 max-w-2xl text-sm text-muted-foreground'>{t('description')}</p>
          </div>
        </div>

        <Button variant='outline' onClick={clearRequestLogs} disabled={logs.length === 0}>
          <EraserIcon />
          {t('clear')}
        </Button>
      </div>

      <div className='grid gap-3 md:grid-cols-4'>
        <MetricCard label={t('metrics.total')} value={totals.total} icon={<ActivityIcon className='size-4' />} />
        <MetricCard label={t('metrics.pending')} value={totals.pending} icon={<RefreshCwIcon className='size-4' />} />
        <MetricCard label={t('metrics.errors')} value={totals.error} icon={<ServerCrashIcon className='size-4' />} />
        <MetricCard label={t('metrics.average')} value={formatDuration(averageDuration)} icon={<Clock3Icon className='size-4' />} />
      </div>

      <Card className='overflow-hidden'>
        <CardHeader className='gap-4 border-b bg-muted/20'>
          <div className='flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between'>
            <CardTitle className='text-base'>{t('table.title')}</CardTitle>
            <div className='flex flex-col gap-2 sm:flex-row sm:items-center'>
              <div className='relative w-full sm:w-72'>
                <SearchIcon className='pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t('searchPlaceholder')}
                  className='pl-8'
                />
              </div>
              <div className='flex rounded-md border bg-background p-0.5'>
                {STATUS_FILTERS.map((item) => (
                  <button
                    key={item}
                    type='button'
                    onClick={() => setStatus(item)}
                    className={cn(
                      'h-8 rounded-sm px-3 text-sm text-muted-foreground transition-colors hover:text-foreground',
                      status === item && 'bg-muted text-foreground shadow-xs',
                    )}
                  >
                    {t(`filters.${item}`)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className='p-0'>
          {filteredLogs.length === 0 ? (
            <div className='flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center'>
              <div className='flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground'>
                <ActivityIcon className='size-5' />
              </div>
              <div>
                <p className='font-medium'>{logs.length === 0 ? t('empty.title') : t('empty.filteredTitle')}</p>
                <p className='mt-1 text-sm text-muted-foreground'>
                  {logs.length === 0 ? t('empty.description') : t('empty.filteredDescription')}
                </p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className='bg-muted/20 hover:bg-muted/20'>
                  <TableHead>{t('table.time')}</TableHead>
                  <TableHead>{t('table.method')}</TableHead>
                  <TableHead>{t('table.path')}</TableHead>
                  <TableHead>{t('table.status')}</TableHead>
                  <TableHead className='text-right'>{t('table.duration')}</TableHead>
                  <TableHead>{t('table.requestId')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredLogs.map((log) => (
                  <RequestLogRow key={log.id} log={log} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
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

function RequestLogRow({ log }: { log: RequestLogEntry }) {
  const { t } = useTranslation('requestLogs')

  return (
    <TableRow>
      <TableCell className='text-muted-foreground tabular-nums'>{formatTime(log.startedAt)}</TableCell>
      <TableCell>
        <span className={cn('font-mono text-xs font-semibold', methodTone(log.method))}>{log.method}</span>
      </TableCell>
      <TableCell className='max-w-[34rem]'>
        <div className='truncate font-mono text-xs text-foreground'>{log.path || '/'}</div>
        {log.errorMessage ? (
          <div className='mt-1 truncate text-xs text-destructive'>{log.errorMessage}</div>
        ) : null}
      </TableCell>
      <TableCell>
        <Badge variant='outline' className={cn('font-mono text-xs', getStatusTone(log.status, log.statusCode))}>
          {log.statusCode ?? t(`status.${log.status}`)}
        </Badge>
      </TableCell>
      <TableCell className='text-right font-mono text-xs tabular-nums text-muted-foreground'>
        {formatDuration(log.durationMs)}
      </TableCell>
      <TableCell className='max-w-44 truncate font-mono text-xs text-muted-foreground'>
        {log.requestId || '-'}
      </TableCell>
    </TableRow>
  )
}
