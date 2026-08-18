import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardTitle } from '#/components/ui/card'
import { cn } from '#/lib/utils'
import { parseObserverStatus } from '../-lib/parse-status'

export interface ParsedQueueRow {
  name: string
  processing: number
  pending: number
  completed: number
  errors: number
  total: number
}

// 将 Observer status 字符串解析为结构化队列数据
function parseQueueStatus(status: string): ParsedQueueRow[] {
  if (!status) return []
  const blocks = parseObserverStatus(status)
  const tableBlock = blocks.find((b) => b.kind === 'table')
  if (!tableBlock) return []

  const headers = tableBlock.headers
  const col = {
    name: headers.findIndex((h) => h === 'Queue'),
    inProgress: headers.findIndex((h) => h === 'In Progress'),
    pending: headers.findIndex((h) => h === 'Pending'),
    processed: headers.findIndex((h) => h === 'Processed'),
    errors: headers.findIndex((h) => h === 'Errors'),
    total: headers.findIndex((h) => h === 'Total'),
  }

  return tableBlock.rows.map((row) => ({
    name: col.name >= 0 ? (row[col.name] ?? '') : '',
    processing: col.inProgress >= 0 ? (parseInt(row[col.inProgress] ?? '0', 10) || 0) : 0,
    pending: col.pending >= 0 ? (parseInt(row[col.pending] ?? '0', 10) || 0) : 0,
    completed: col.processed >= 0 ? (parseInt(row[col.processed] ?? '0', 10) || 0) : 0,
    errors: col.errors >= 0 ? (parseInt(row[col.errors] ?? '0', 10) || 0) : 0,
    total: col.total >= 0 ? (parseInt(row[col.total] ?? '0', 10) || 0) : 0,
  }))
}

export interface QueueStatusCardProps {
  title?: string
  /** Observer system 返回的 queue 组件的 status 原始文本 */
  status?: string
  isHealthy?: boolean
  customRows?: ParsedQueueRow[]
}

export function QueueStatusCard({ title, status = '', isHealthy = true, customRows }: QueueStatusCardProps) {
  const { t } = useTranslation('monitoringPage')
  const parsedFromStatus = React.useMemo(() => parseQueueStatus(status), [status])
  const rows = customRows ?? parsedFromStatus

  const nonTotalRows = React.useMemo(
    () => rows.filter((r) => r.name.toUpperCase() !== 'TOTAL'),
    [rows],
  )
  const totalRow = React.useMemo(
    () => rows.find((r) => r.name.toUpperCase() === 'TOTAL'),
    [rows],
  )

  const getQueueDisplayName = (name: string): string => {
    const lower = name.toLowerCase()
    if (lower === 'total') return t('queue.totalRow')
    if (lower.includes('embedding')) return t('queue.embedding')
    if (lower.includes('semantic-node') || lower.includes('semantic_node')) return t('queue.semanticNodes')
    if (lower.includes('semantic')) return t('queue.semantic')
    if (lower.includes('externalparse') || lower.includes('external_parse')) return t('queue.externalParse')
    if (lower.includes('sessioncommit') || lower.includes('session_commit')) return t('queue.sessionCommit')
    return name
  }

  const renderRow = (row: ParsedQueueRow, isTotalRow: boolean) => {
    const displayName = getQueueDisplayName(row.name)
    return (
      <div
        key={row.name}
        className={cn(
          'grid grid-cols-6 items-center px-2 py-1 text-[11px] rounded font-mono transition-colors',
          isTotalRow
            ? 'mt-auto bg-muted/60 font-bold border border-border/80 text-foreground'
            : 'bg-muted/20 hover:bg-muted/40 text-foreground/90',
        )}
      >
        {/* 队列名 */}
        <span className="col-span-2 font-sans font-medium truncate">{displayName}</span>

        {/* 处理中 */}
        <span
          className={cn(
            'text-right tabular-nums font-bold',
            row.processing > 0
              ? 'text-blue-600 dark:text-blue-400'
              : 'text-muted-foreground/60',
          )}
        >
          {row.processing}
        </span>

        {/* 待处理 */}
        <span
          className={cn(
            'text-right tabular-nums font-bold',
            row.pending > 0
              ? 'text-amber-600 dark:text-amber-400'
              : 'text-muted-foreground/60',
          )}
        >
          {row.pending}
        </span>

        {/* 已完成 */}
        <span
          className={cn(
            'text-right tabular-nums font-bold',
            row.completed > 0
              ? 'text-emerald-600 dark:text-emerald-400'
              : 'text-muted-foreground/60',
          )}
        >
          {row.completed.toLocaleString()}
        </span>

        {/* 错误数 */}
        <span
          className={cn(
            'text-right tabular-nums font-bold',
            row.errors > 0 ? 'text-destructive' : 'text-muted-foreground/60',
          )}
        >
          {row.errors}
        </span>
      </div>
    )
  }

  return (
    <Card className="flex h-full flex-col justify-between gap-2 p-2.5 shadow-none transition-colors hover:border-primary/30">
      <div className="flex items-center justify-between gap-2">
        <CardTitle className="text-sm font-semibold">{title ?? t('queue.title')}</CardTitle>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border bg-muted/20 p-2.5 text-center text-xs text-muted-foreground">
          {t('queue.noData')}
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-between gap-1">
          {/* 统一顶置表头 */}
          <div className="grid grid-cols-6 items-center px-2 py-0.5 text-[11px] text-muted-foreground font-medium border-b border-border/50">
            <span className="col-span-2">{t('queue.queueName')}</span>
            <span className="text-right">{t('queue.processing')}</span>
            <span className="text-right">{t('queue.pending')}</span>
            <span className="text-right">{t('queue.completed')}</span>
            <span className="text-right">{t('queue.errors')}</span>
          </div>

          {/* 数据列表 */}
          <div className="flex flex-col gap-1">
            {nonTotalRows.map((row) => renderRow(row, false))}
          </div>

          {/* 底端对齐合计行 */}
          {totalRow ? renderRow(totalRow, true) : null}
        </div>
      )}
    </Card>
  )
}
