import { useId, useState } from 'react'
import { ChevronRightIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { TableCell, TableRow } from '#/components/ui/table'
import { cn } from '#/lib/utils'
import type { ConsoleAuditLogItem } from '@ov-server/api/v1/console'

import {
  formatDuration,
  formatTime,
  getStatusTone,
  methodTone,
  normalizeStatus,
} from '../-lib/format'

type RequestLogRowProps = {
  log: ConsoleAuditLogItem
}

export function RequestLogRow({ log }: RequestLogRowProps) {
  const { t } = useTranslation('requestLogs')
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const status = normalizeStatus(log.status_code)
  const method = log.method ?? '-'
  const isSlow = (log.duration_ms ?? 0) > 1000
  const hasErrorDetails = Boolean(
    log.error_code || log.error_message || log.error_details,
  )
  const hasStructuredDetails = Boolean(
    log.error_details && Object.keys(log.error_details).length > 0,
  )
  const serializedDetails = hasStructuredDetails
    ? JSON.stringify(log.error_details, null, 2)
    : null

  const toggleExpanded = () => {
    if (hasErrorDetails) {
      setExpanded((value) => !value)
    }
  }

  return (
    <>
      <TableRow
        className={cn(hasErrorDetails && 'cursor-pointer')}
        onClick={hasErrorDetails ? toggleExpanded : undefined}
      >
        <TableCell className="text-muted-foreground tabular-nums">
          {formatTime(log.created_at)}
        </TableCell>
        <TableCell className="max-w-40 truncate font-mono text-xs text-muted-foreground">
          {log.api_type || '-'}
        </TableCell>
        <TableCell>
          <span
            className={cn(
              'font-mono text-xs font-semibold',
              methodTone(method),
            )}
          >
            {method}
          </span>
        </TableCell>
        <TableCell className="max-w-[34rem]">
          <div className="truncate font-mono text-xs text-foreground">
            {log.route || '/'}
          </div>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-1">
            <Badge
              variant="outline"
              className={cn(
                'font-mono text-xs',
                getStatusTone(status, log.status_code),
              )}
            >
              {log.status_code ?? t(`status.${status}`)}
            </Badge>
            {hasErrorDetails ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-controls={detailsId}
                aria-expanded={expanded}
                aria-label={t(expanded ? 'details.hide' : 'details.show')}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleExpanded()
                }}
              >
                <ChevronRightIcon
                  className={cn(
                    'transition-transform',
                    expanded && 'rotate-90',
                  )}
                />
              </Button>
            ) : null}
          </div>
        </TableCell>
        <TableCell
          className={cn(
            'text-right font-mono text-xs tabular-nums text-muted-foreground',
            isSlow && 'font-semibold text-amber-600 dark:text-amber-300',
          )}
        >
          {formatDuration(log.duration_ms)}
        </TableCell>
        <TableCell className="max-w-44 truncate font-mono text-xs text-muted-foreground">
          {log.request_id || '-'}
        </TableCell>
        <TableCell className="max-w-36 truncate font-mono text-xs text-muted-foreground">
          {log.account_id || '-'}
        </TableCell>
        <TableCell className="max-w-36 truncate font-mono text-xs text-muted-foreground">
          {log.user_id || '-'}
        </TableCell>
      </TableRow>
      {hasErrorDetails && expanded ? (
        <TableRow id={detailsId} className="bg-muted/20 hover:bg-muted/20">
          <TableCell colSpan={9} className="whitespace-normal px-4 py-3">
            <div className="grid gap-3 text-sm md:grid-cols-[12rem_minmax(0,1fr)]">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  {t('details.code')}
                </div>
                <div className="mt-1 font-mono text-xs text-foreground">
                  {log.error_code || '-'}
                </div>
              </div>
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  {t('details.message')}
                </div>
                <div className="mt-1 break-words text-foreground">
                  {log.error_message || '-'}
                </div>
              </div>
              {serializedDetails ? (
                <div className="md:col-span-2">
                  <div className="text-xs font-medium text-muted-foreground">
                    {t('details.data')}
                  </div>
                  <pre className="mt-1 overflow-x-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap text-foreground">
                    {serializedDetails}
                  </pre>
                </div>
              ) : null}
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}
