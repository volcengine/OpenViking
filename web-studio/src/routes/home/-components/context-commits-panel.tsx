import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import HeatMap from '@uiw/react-heat-map'

import { Skeleton } from '#/components/ui/skeleton'

import {
  COMMIT_SERIES_DAYS,
  HEATMAP_COLOR_STOPS,
  HEATMAP_EMPTY_COLOR,
  HEATMAP_MONTH_LABELS,
  HEATMAP_WEEK_LABELS,
} from '../-constants/dashboard'
import type {
  CommitTooltip,
  ConsoleSeries,
  ContextCommitItem,
  HeatMapDayValue,
  HomeT,
} from '../-types/dashboard'
import {
  asNumber,
  formatNumber,
  formatShortDate,
  getLastDaysRange,
  isDisabledPayload,
  parseDateKey,
} from '../-lib/format'
import {
  buildHeatmapPanelColors,
  computeCommitHeatmapStats,
  getHeatmapFillColor,
  normalizeCommitHeatmapData,
} from '../-lib/normalize'
import { EmptyState, Panel, SectionHeading } from './panel'

function ContextCommitStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-border/60 py-2 last:border-b-0 sm:border-b-0 sm:border-r sm:px-4 sm:last:border-r-0 xl:border-b xl:border-r-0 xl:px-0 xl:last:border-b-0">
      <div className="text-[11px] leading-none text-muted-foreground">
        {label}
      </div>
      <div className="mt-1.5 text-lg font-semibold leading-none tabular-nums">
        {value}
      </div>
    </div>
  )
}

export function ContextCommitsPanel({
  data,
  isError,
  isLoading,
  t,
}: {
  data: ConsoleSeries<ContextCommitItem> | undefined
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const [tooltip, setTooltip] = useState<CommitTooltip | null>(null)
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
  const disabled = isDisabledPayload(data)
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
        <>
          <div className="grid gap-4 xl:grid-cols-[minmax(820px,auto)_minmax(180px,1fr)]">
            <div className="min-w-0">
              <div className="overflow-x-auto">
                <HeatMap
                  className="[--heatmap-empty:oklch(0.92_0_0)] text-muted-foreground dark:[--heatmap-empty:oklch(0.31_0_0)] [&_.w-heatmap-month]:fill-current [&_.w-heatmap-week]:fill-current"
                  endDate={endDate}
                  height={128}
                  legendCellSize={0}
                  monthLabels={HEATMAP_MONTH_LABELS}
                  panelColors={panelColors}
                  rectProps={{ rx: 2 }}
                  rectRender={(props, item) => {
                    const value = item as Partial<HeatMapDayValue>
                    const heatmapItem = value.details
                      ? (value as HeatMapDayValue)
                      : null
                    const count = asNumber(value.count)
                    const fill = getHeatmapFillColor(count, panelColors)
                    return (
                      <rect
                        {...props}
                        fill={fill}
                        onMouseEnter={(event) => {
                          if (!heatmapItem) return
                          const rect = (
                            event.target as SVGRectElement
                          ).getBoundingClientRect()
                          setTooltip({
                            item: heatmapItem,
                            x: rect.left + rect.width / 2,
                            y: rect.top,
                          })
                        }}
                        onMouseLeave={() => setTooltip(null)}
                        style={{
                          ...props.style,
                          cursor: heatmapItem ? 'pointer' : 'default',
                          fill,
                          transition: 'fill 0.15s, opacity 0.15s',
                        }}
                      />
                    )
                  }}
                  rectSize={11}
                  space={3}
                  startDate={startDate}
                  value={items}
                  weekLabels={HEATMAP_WEEK_LABELS}
                  width={820}
                />
              </div>

              <div className="-mt-1 flex justify-end text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <span className="mr-0.5">
                    {t('contextCommits.legend.none')}
                  </span>
                  <span
                    className="size-3 rounded-[2px]"
                    style={{ backgroundColor: HEATMAP_EMPTY_COLOR }}
                  />
                  {HEATMAP_COLOR_STOPS.map((color, index) => (
                    <span
                      key={`${color}-${index}`}
                      className="size-3 rounded-[2px]"
                      style={{ backgroundColor: color }}
                    />
                  ))}
                  <span className="ml-0.5">
                    {t('contextCommits.legend.more')}
                  </span>
                </div>
              </div>
            </div>

            <div className="grid content-start border-t border-border/60 pt-3 sm:grid-cols-3 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-5">
              <ContextCommitStat
                label={t('contextCommits.stats.activeDays')}
                value={formatNumber(stats.activeDays)}
              />
              <ContextCommitStat
                label={t('contextCommits.stats.peakDay')}
                value={formatNumber(stats.peakCount)}
              />
              <ContextCommitStat
                label={t('contextCommits.stats.recentDay')}
                value={
                  stats.recentDate ? formatShortDate(stats.recentDate) : '--'
                }
              />
            </div>
          </div>
        </>
      )}

      {tooltip && typeof document !== 'undefined'
        ? createPortal(
            <CommitTooltipView
              item={tooltip.item}
              t={t}
              x={tooltip.x}
              y={tooltip.y}
            />,
            document.body,
          )
        : null}
    </Panel>
  )
}

function CommitTooltipView({
  item,
  t,
  x,
  y,
}: {
  item: HeatMapDayValue
  t: HomeT
  x: number
  y: number
}) {
  const details = item.details
  const rows = [
    {
      label: t('contextCommits.operations.addResource'),
      value: details.add_resource,
    },
    {
      label: t('contextCommits.operations.addSkill'),
      value: details.add_skill,
    },
    {
      label: t('contextCommits.operations.sessionAddMessage'),
      value: details.session_add_message,
    },
    {
      label: t('contextCommits.operations.sessionCommit'),
      value: details.session_commit,
    },
  ]

  return (
    <div
      className="pointer-events-none fixed z-50 w-64 rounded-xl border border-border/70 bg-popover/95 px-3.5 py-3 text-xs text-popover-foreground shadow-2xl shadow-black/10 ring-1 ring-foreground/5 backdrop-blur-md dark:shadow-black/35"
      style={{
        left: x,
        top: y - 12,
        transform: 'translate(-50%, -100%)',
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-medium tabular-nums">{details.date}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {t('contextCommits.tooltip.total')}
          </div>
        </div>
        <div className="rounded-md bg-[oklch(0.68_0.12_232_/_0.14)] px-2 py-1 text-sm font-semibold tabular-nums text-[oklch(0.45_0.13_242)] dark:bg-[oklch(0.68_0.14_232_/_0.18)] dark:text-[oklch(0.76_0.14_232)]">
          {details.total}
        </div>
      </div>

      <div className="mt-3 space-y-2 border-t border-border/70 pt-3">
        {rows.map((row, index) => (
          <div
            key={row.label}
            className="grid grid-cols-[auto_1fr_auto] items-center gap-2"
          >
            <span
              className="size-1.5 rounded-full"
              style={{
                backgroundColor:
                  HEATMAP_COLOR_STOPS[
                    Math.min(index, HEATMAP_COLOR_STOPS.length - 1)
                  ],
                opacity: row.value > 0 ? 1 : 0.35,
              }}
            />
            <span className="min-w-0 truncate text-muted-foreground">
              {row.label}
            </span>
            <span className="font-medium tabular-nums">
              {formatNumber(row.value)}
            </span>
          </div>
        ))}
      </div>

      <span className="absolute left-1/2 top-full size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-border/70 bg-popover/95" />
    </div>
  )
}
