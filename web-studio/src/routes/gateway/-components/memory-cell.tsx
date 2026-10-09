import type * as React from 'react'
import { CircleAlertIcon, RotateCcwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { LogRecord } from '../-lib/api'
import { formatDuration } from '../-lib/format'
import { recallReasonLabel } from '../-lib/localize'
import { EmptyValue } from './status-badges'

/** Recall outcomes that are not failures. */
const RECALL_OUTCOMES = new Set(['recalled', 'empty', 'disabled'])

/** A recall that failed, as opposed to finding nothing or being off. */
export function recallFailed(record: LogRecord): boolean {
  return Boolean(
    record.recall_reason && !RECALL_OUTCOMES.has(record.recall_reason),
  )
}

/** Compact visible text with a full description for hover and screen readers. */
export function Described({
  description,
  className,
  children,
}: {
  description: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <span title={description} className={className}>
      <span aria-hidden className="inline-flex items-center gap-1">
        {children}
      </span>
      <span className="sr-only">{description}</span>
    </span>
  )
}

/**
 * Memory of one request: entries recalled (+N with the recall time), a failed
 * recall, and earlier memory kept in the history (↺ N); a dash when none,
 * unless `hideEmpty` because something else fills the cell.
 */
export function MemoryCell({
  record,
  hideEmpty = false,
}: {
  record: LogRecord
  hideEmpty?: boolean
}) {
  const { t, i18n } = useTranslation('contextGateway')
  const locale = i18n.resolvedLanguage
  const recalled = record.recall_count ?? 0
  const replayed = record.replay_hits ?? 0
  const failed = recallFailed(record)
  if (!recalled && !replayed && !failed) {
    return hideEmpty ? null : <EmptyValue />
  }
  return (
    <div className="flex items-center gap-3 text-xs tabular-nums">
      {recalled > 0 ? (
        <Described
          description={t('requests.memory.recalled', {
            count: recalled,
            duration: formatDuration(record.recall_ms ?? 0, locale),
          })}
        >
          <span className="font-medium text-emerald-700 dark:text-emerald-300">
            +{recalled}
          </span>
          <span className="text-muted-foreground">
            {formatDuration(record.recall_ms ?? 0, locale)}
          </span>
        </Described>
      ) : null}
      {failed ? (
        <Described
          description={recallReasonLabel(t, record.recall_reason)}
          className="text-amber-700 dark:text-amber-300"
        >
          <CircleAlertIcon className="size-3.5" />
          {t('requests.memory.recallFailed')}
        </Described>
      ) : null}
      {replayed > 0 ? (
        <Described
          description={t('requests.memory.replayed', { count: replayed })}
          className="text-muted-foreground"
        >
          <RotateCcwIcon className="size-3" />
          {replayed}
        </Described>
      ) : null}
    </div>
  )
}
