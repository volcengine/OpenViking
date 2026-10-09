import type * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ActivityIcon, ArrowRightIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { cn } from '#/lib/utils'

import type { GatewayError, LogRecord } from '../-lib/api'
import { formatDateTime, formatRelativeTime } from '../-lib/format'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import { MemoryCell } from './memory-cell'
import {
  EmptyValue,
  IssueBadge,
  KindBadge,
  RequestStatus,
} from './status-badges'

/** Memory column: what was recalled or replayed, and any degradation. */
function RecentMemory({ log }: { log: LogRecord }) {
  return (
    <div className="flex items-center gap-3">
      <MemoryCell record={log} hideEmpty={Boolean(log.degradation)} />
      {log.degradation ? <IssueBadge degradation={log.degradation} /> : null}
    </div>
  )
}

/**
 * Columns left out below `md`; the time cell then shows the model and the
 * status cell an issue icon instead.
 */
const DESKTOP_ONLY = 'hidden md:table-cell'

type RecentRequestsProps = {
  /** The newest model requests (memory-sync events left out). */
  logs?: LogRecord[]
  error: GatewayError | null
}

/** The newest model requests in a compact table, with a link to all. */
export function RecentRequests({ logs, error }: RecentRequestsProps) {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage

  let body: React.ReactNode
  if (logs === undefined) {
    body = error ? (
      <ErrorState
        className="min-h-40"
        title={t('requests.loadFailed')}
        error={error}
      />
    ) : (
      <LoadingState className="min-h-40" />
    )
  } else if (logs.length === 0) {
    body = (
      <EmptyState
        className="min-h-40"
        icon={<ActivityIcon />}
        title={t('overview.recent.empty.title')}
        description={t('overview.recent.empty.description')}
      />
    )
  } else {
    body = (
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/20 hover:bg-muted/20">
            <TableHead className="pl-5">
              {t('overview.recent.columns.time')}
            </TableHead>
            <TableHead>{t('overview.recent.columns.type')}</TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('overview.recent.columns.model')}
            </TableHead>
            <TableHead className="max-md:pr-5">
              {t('overview.recent.columns.status')}
            </TableHead>
            <TableHead className={cn(DESKTOP_ONLY, 'pr-5')}>
              {t('overview.recent.columns.memory')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {logs.map((log, index) => (
            <TableRow key={log.request_id ?? `${log.time}-${index}`}>
              <TableCell
                className="pl-5 text-muted-foreground"
                title={formatDateTime(log.time, locale, true)}
              >
                {formatRelativeTime(log.time, locale)}
                {log.model ? (
                  <span
                    className="block max-w-28 truncate font-mono text-xs md:hidden"
                    title={log.model}
                  >
                    {log.model}
                  </span>
                ) : null}
              </TableCell>
              <TableCell>
                <KindBadge kind={log.kind} />
              </TableCell>
              <TableCell
                className={cn(
                  DESKTOP_ONLY,
                  'max-w-56 truncate font-mono text-xs',
                )}
                title={log.model}
              >
                {log.model || <EmptyValue />}
              </TableCell>
              <TableCell className="max-md:pr-5">
                <RequestStatus
                  status={log.status}
                  degradation={log.degradation}
                />
              </TableCell>
              <TableCell className={cn(DESKTOP_ONLY, 'pr-5')}>
                <RecentMemory log={log} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )
  }

  return (
    <Card className="gap-0 overflow-hidden py-0">
      <CardHeader className="border-b px-5 py-3 [.border-b]:pb-3">
        <CardTitle className="self-center">
          {t('overview.recent.title')}
        </CardTitle>
        <CardAction className="self-center">
          <Button
            size="sm"
            variant="ghost"
            nativeButton={false}
            render={<Link to="/gateway/requests" />}
          >
            {t('actions.viewAll')}
            <ArrowRightIcon />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0">{body}</CardContent>
    </Card>
  )
}
