import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
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
  const status = normalizeStatus(log.status_code)
  const method = log.method ?? '-'
  const isSlow = (log.duration_ms ?? 0) > 1000

  return (
    <TableRow>
      <TableCell className="text-muted-foreground tabular-nums">
        {formatTime(log.created_at)}
      </TableCell>
      <TableCell className="max-w-40 truncate font-mono text-xs text-muted-foreground">
        {log.api_type || '-'}
      </TableCell>
      <TableCell>
        <span
          className={cn('font-mono text-xs font-semibold', methodTone(method))}
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
        <Badge
          variant="outline"
          className={cn(
            'font-mono text-xs',
            getStatusTone(status, log.status_code),
          )}
        >
          {log.status_code ?? t(`status.${status}`)}
        </Badge>
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
  )
}
