import * as React from 'react'
import { ChevronRightIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { cn } from '#/lib/utils'

import type { GatewayKey, LogRecord, Upstream } from '../-lib/api'
import {
  formatCompact,
  formatDateTime,
  formatPercent,
  formatRelativeTime,
} from '../-lib/format'
import { Described, MemoryCell } from './memory-cell'
import { RequestDetails, tokenLine } from './request-details'
import type { ResyncRequest } from './request-details'
import {
  CaptureStatusBadge,
  EmptyValue,
  IssueBadge,
  KindBadge,
  RequestStatus,
} from './status-badges'

function TokensCell({ record }: { record: LogRecord }) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const {
    input_tokens: input,
    cached_tokens: cached,
    output_tokens: output,
  } = record
  // Token counts and memory sync report no usage, or zeros.
  if (!input && !output) return <EmptyValue />
  const parts = [
    input === undefined
      ? null
      : t('requests.tokens.input', { value: formatCompact(input, locale) }),
    cached === undefined || !input
      ? null
      : t('requests.tokens.cached', {
          value: formatPercent(cached / input, locale),
        }),
    output === undefined
      ? null
      : t('requests.tokens.output', { value: formatCompact(output, locale) }),
  ].filter((part): part is string => Boolean(part))
  const full = tokenLine(
    t,
    { input, cached, cacheWrite: record.cache_write_tokens, output },
    locale,
  )
  return (
    <Described description={full} className="text-xs tabular-nums">
      {parts.join(' · ')}
    </Described>
  )
}

/**
 * Columns left out below `md`, so a row keeps its time, type, status and
 * toggle on screen. The time cell then shows the model, the status cell an
 * issue icon, and the details panel already has tokens, memory and saving.
 */
const DESKTOP_ONLY = 'hidden md:table-cell'

/** Column headers of the request table, kept next to the row that fills them. */
export function RequestsTableHeader() {
  const { t } = useTranslation('contextGateway')
  return (
    <TableHeader>
      <TableRow className="bg-muted/20 hover:bg-muted/20">
        <TableHead>{t('requests.table.time')}</TableHead>
        <TableHead>{t('requests.table.type')}</TableHead>
        <TableHead className={DESKTOP_ONLY}>
          {t('requests.table.model')}
        </TableHead>
        <TableHead>{t('requests.table.status')}</TableHead>
        <TableHead className={DESKTOP_ONLY}>
          {t('requests.table.tokens')}
        </TableHead>
        <TableHead className={DESKTOP_ONLY}>
          {t('requests.table.memory')}
        </TableHead>
        <TableHead className={DESKTOP_ONLY}>
          {t('requests.table.saving')}
        </TableHead>
        <TableHead className={DESKTOP_ONLY}>
          {t('requests.table.issue')}
        </TableHead>
        <TableHead className="w-8 md:w-10">
          <span className="sr-only">{t('requests.table.details')}</span>
        </TableHead>
      </TableRow>
    </TableHeader>
  )
}

const COLUMN_COUNT = 9

type RequestRowProps = {
  record: LogRecord
  /** Upstreams by id; undefined until the list has loaded. */
  upstreams?: Map<string, Upstream>
  /** Gateway keys by id; undefined until the list has loaded. */
  keys?: Map<string, GatewayKey>
  onResync: (request: ResyncRequest) => void
}

/** One request-log entry with an expandable detail panel. */
export function RequestRow({
  record,
  upstreams,
  keys,
  onResync,
}: RequestRowProps) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const [expanded, setExpanded] = React.useState(false)
  const detailsId = React.useId()
  const toggle = () => setExpanded((value) => !value)

  return (
    <>
      <TableRow
        className={cn('cursor-pointer', expanded && 'border-b-0')}
        onClick={toggle}
      >
        <TableCell
          className="text-muted-foreground tabular-nums"
          title={formatDateTime(record.time, locale, true)}
        >
          {formatRelativeTime(record.time, locale)}
          {record.model ? (
            <span
              className="block max-w-24 truncate font-mono text-xs md:hidden"
              title={record.model}
            >
              {record.model}
            </span>
          ) : null}
        </TableCell>
        <TableCell>
          <KindBadge kind={record.kind} />
        </TableCell>
        <TableCell
          className={cn(DESKTOP_ONLY, 'max-w-48 truncate font-mono text-xs')}
          title={record.model}
        >
          {record.model || <EmptyValue />}
        </TableCell>
        <TableCell>
          <RequestStatus
            status={record.status}
            degradation={record.degradation}
          />
        </TableCell>
        <TableCell className={DESKTOP_ONLY}>
          <TokensCell record={record} />
        </TableCell>
        <TableCell className={DESKTOP_ONLY}>
          <MemoryCell record={record} />
        </TableCell>
        <TableCell className={DESKTOP_ONLY}>
          <CaptureStatusBadge
            status={record.capture_status}
            reason={record.capture_reason}
          />
        </TableCell>
        <TableCell className={DESKTOP_ONLY}>
          {record.degradation ? (
            <IssueBadge degradation={record.degradation} />
          ) : null}
        </TableCell>
        <TableCell className="text-right max-md:pl-0">
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-expanded={expanded}
            aria-controls={detailsId}
            aria-label={t(
              expanded ? 'requests.details.hide' : 'requests.details.show',
            )}
            onClick={(event) => {
              event.stopPropagation()
              toggle()
            }}
          >
            <ChevronRightIcon
              className={cn('transition-transform', expanded && 'rotate-90')}
            />
          </Button>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow id={detailsId} className="bg-muted/20 hover:bg-muted/20">
          <TableCell
            colSpan={COLUMN_COUNT}
            className="px-4 py-4 whitespace-normal"
          >
            <RequestDetails
              record={record}
              upstreams={upstreams}
              keys={keys}
              onResync={onResync}
            />
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}
