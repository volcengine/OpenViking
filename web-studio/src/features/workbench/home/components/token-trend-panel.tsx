import { lazy, Suspense } from 'react'

import { Skeleton } from '#/shared/ui/skeleton'

import {
  EmptyState,
  Panel,
  SectionHeading,
} from '#/features/workbench/home/components/panel'
import { TOKEN_SERIES_DAYS } from '#/features/workbench/home/constants/dashboard'
import {
  getLastDaysRange,
  isDisabledPayload,
} from '#/features/workbench/home/lib/format'
import { normalizeTokenSeries } from '#/features/workbench/home/lib/normalize'
import type {
  ConsoleSeries,
  HomeT,
  TokenSeriesItem,
} from '#/features/workbench/home/types/dashboard'

const TokenTrendChart = lazy(() =>
  import('#/features/workbench/home/components/token-trend-chart').then(
    (module) => ({
      default: module.TokenTrendChart,
    }),
  ),
)

export function TokenTrendPanel({
  data,
  disabled: disabledProp,
  disabledMessage,
  isError,
  isLoading,
  t,
}: {
  data: ConsoleSeries<TokenSeriesItem> | undefined
  disabled?: boolean
  disabledMessage?: string
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const items = normalizeTokenSeries(data?.items)
  const disabled = Boolean(disabledProp) || isDisabledPayload(data)
  const rangeLabel =
    data?.start_date && data.end_date
      ? `${data.start_date} - ${data.end_date}`
      : `${getLastDaysRange(TOKEN_SERIES_DAYS).startDate} - ${getLastDaysRange(TOKEN_SERIES_DAYS).endDate}`

  return (
    <Panel>
      <SectionHeading
        action={
          <span className="pt-1 text-xs tabular-nums text-muted-foreground">
            {rangeLabel}
          </span>
        }
        description={t('tokenTrend.description')}
        title={t('tokenTrend.title')}
      />

      {isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : isError ? (
        <EmptyState>{t('requestFailed')}</EmptyState>
      ) : disabled ? (
        <EmptyState>{disabledMessage ?? t('usageDisabled')}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t('tokenTrend.empty')}</EmptyState>
      ) : (
        <Suspense fallback={<Skeleton className="h-72 w-full" />}>
          <TokenTrendChart items={items} t={t} />
        </Suspense>
      )}
    </Panel>
  )
}
