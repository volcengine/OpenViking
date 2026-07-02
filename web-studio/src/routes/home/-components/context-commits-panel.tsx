import { lazy, Suspense, useMemo } from 'react'

import { Skeleton } from '#/components/ui/skeleton'

import { COMMIT_SERIES_DAYS } from '../-constants/dashboard'
import type {
  ConsoleSeries,
  ContextCommitItem,
  HomeT,
} from '../-types/dashboard'
import {
  formatNumber,
  getLastDaysRange,
  isDisabledPayload,
  parseDateKey,
} from '../-lib/format'
import {
  buildHeatmapPanelColors,
  computeCommitHeatmapStats,
  normalizeCommitHeatmapData,
} from '../-lib/normalize'
import { EmptyState, Panel, SectionHeading } from './panel'

const ContextCommitsHeatmap = lazy(() =>
  import('./context-commits-heatmap').then((module) => ({
    default: module.ContextCommitsHeatmap,
  })),
)

export function ContextCommitsPanel({
  data,
  disabled: disabledProp,
  isError,
  isLoading,
  t,
}: {
  data: ConsoleSeries<ContextCommitItem> | undefined
  disabled?: boolean
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const items = useMemo(
    () => normalizeCommitHeatmapData(data?.items),
    [data?.items],
  )
  const panelColors = useMemo(() => buildHeatmapPanelColors(items), [items])
  const totalCommits = useMemo(
    () => items.reduce((total, item) => total + item.count, 0),
    [items],
  )
  const stats = useMemo(() => computeCommitHeatmapStats(items), [items])
  const disabled = Boolean(disabledProp) || isDisabledPayload(data)
  const rangeLabel =
    data?.start_date && data.end_date
      ? `${data.start_date} - ${data.end_date}`
      : `${getLastDaysRange(COMMIT_SERIES_DAYS).startDate} - ${getLastDaysRange(COMMIT_SERIES_DAYS).endDate}`
  const range = getLastDaysRange(COMMIT_SERIES_DAYS)
  const startDate = parseDateKey(data?.start_date ?? range.startDate)
  const endDate = parseDateKey(data?.end_date ?? range.endDate)
  const title =
    !isLoading && !isError && !disabled
      ? totalCommits > 0
        ? t('contextCommits.yearlyTotal', { count: formatNumber(totalCommits) })
        : t('contextCommits.yearlyEmpty')
      : t('contextCommits.title')

  return (
    <Panel>
      <SectionHeading
        action={
          <span className="pt-1 text-xs tabular-nums text-muted-foreground">
            {rangeLabel}
          </span>
        }
        description={t('contextCommits.description')}
        title={title}
      />

      {isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : isError ? (
        <EmptyState>{t('requestFailed')}</EmptyState>
      ) : disabled ? (
        <EmptyState>{t('usageDisabled')}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t('contextCommits.empty')}</EmptyState>
      ) : (
        <Suspense fallback={<Skeleton className="h-72 w-full" />}>
          <ContextCommitsHeatmap
            endDate={endDate}
            items={items}
            panelColors={panelColors}
            startDate={startDate}
            stats={stats}
            t={t}
          />
        </Suspense>
      )}
    </Panel>
  )
}
