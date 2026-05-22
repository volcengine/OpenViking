import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Skeleton } from '#/components/ui/skeleton'

import { TOKEN_COLORS, TOKEN_SERIES_DAYS } from '../-constants/dashboard'
import type {
  ConsoleSeries,
  HomeT,
  TokenSeriesItem,
  TokenTrendPayload,
} from '../-types/dashboard'
import {
  formatNumber,
  formatShortDate,
  getLastDaysRange,
  isDisabledPayload,
} from '../-lib/format'
import { normalizeTokenSeries } from '../-lib/normalize'
import { EmptyState, Panel, SectionHeading } from './panel'

export function TokenTrendPanel({
  data,
  isError,
  isLoading,
  t,
}: {
  data: ConsoleSeries<TokenSeriesItem> | undefined
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const items = normalizeTokenSeries(data?.items)
  const disabled = isDisabledPayload(data)
  const rangeLabel =
    data?.start_date && data.end_date
      ? `${data.start_date} - ${data.end_date}`
      : `${getLastDaysRange(TOKEN_SERIES_DAYS).startDate} - ${getLastDaysRange(TOKEN_SERIES_DAYS).endDate}`

  return (
    <Panel>
      <SectionHeading
        action={
          <span className="rounded-full border border-[oklch(0.68_0.12_232/0.2)] bg-background/70 px-3 py-1 text-xs tabular-nums text-muted-foreground shadow-xs dark:bg-white/[0.06]">
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
        <EmptyState>{t('usageDisabled')}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t('tokenTrend.empty')}</EmptyState>
      ) : (
        <>
          <div className="h-72 min-h-72 min-w-0 w-full">
            <ResponsiveContainer
              width="100%"
              height="100%"
              initialDimension={{ width: 720, height: 288 }}
              minWidth={1}
              minHeight={1}
            >
              <AreaChart
                data={items}
                margin={{ bottom: 0, left: 0, right: 12, top: 8 }}
              >
                <defs>
                  <linearGradient
                    id="tokenTrendVlmInput"
                    x1="0"
                    x2="0"
                    y1="0"
                    y2="1"
                  >
                    <stop
                      offset="5%"
                      stopColor={TOKEN_COLORS.input}
                      stopOpacity={0.52}
                    />
                    <stop
                      offset="95%"
                      stopColor={TOKEN_COLORS.input}
                      stopOpacity={0.14}
                    />
                  </linearGradient>
                  <linearGradient
                    id="tokenTrendVlmOutput"
                    x1="0"
                    x2="0"
                    y1="0"
                    y2="1"
                  >
                    <stop
                      offset="5%"
                      stopColor={TOKEN_COLORS.output}
                      stopOpacity={0.46}
                    />
                    <stop
                      offset="95%"
                      stopColor={TOKEN_COLORS.output}
                      stopOpacity={0.11}
                    />
                  </linearGradient>
                  <linearGradient
                    id="tokenTrendEmbedding"
                    x1="0"
                    x2="0"
                    y1="0"
                    y2="1"
                  >
                    <stop
                      offset="5%"
                      stopColor={TOKEN_COLORS.embedding}
                      stopOpacity={0.36}
                    />
                    <stop
                      offset="95%"
                      stopColor={TOKEN_COLORS.embedding}
                      stopOpacity={0.08}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  stroke="currentColor"
                  strokeOpacity={0.08}
                  vertical={false}
                />
                <XAxis
                  axisLine={false}
                  className="text-muted-foreground"
                  dataKey="date"
                  tick={{ fill: 'currentColor', fontSize: 12 }}
                  tickFormatter={formatShortDate}
                  tickLine={false}
                />
                <YAxis
                  axisLine={false}
                  className="text-muted-foreground"
                  tick={{ fill: 'currentColor', fontSize: 12 }}
                  tickFormatter={(value) => Number(value).toLocaleString()}
                  tickLine={false}
                  width={64}
                />
                <Tooltip
                  cursor={{ stroke: 'currentColor', strokeOpacity: 0.12 }}
                  content={<TokenTrendTooltip t={t} />}
                />
                <Area
                  dataKey="vlm_input"
                  fill="url(#tokenTrendVlmInput)"
                  name="vlm_input"
                  stackId="tokens"
                  stroke={TOKEN_COLORS.input}
                  strokeWidth={2}
                  type="monotone"
                />
                <Area
                  dataKey="vlm_output"
                  fill="url(#tokenTrendVlmOutput)"
                  name="vlm_output"
                  stackId="tokens"
                  stroke={TOKEN_COLORS.output}
                  strokeWidth={2}
                  type="monotone"
                />
                <Area
                  dataKey="embedding_input"
                  fill="url(#tokenTrendEmbedding)"
                  name="embedding_input"
                  stackId="tokens"
                  stroke={TOKEN_COLORS.embedding}
                  strokeWidth={2}
                  type="monotone"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-4 flex flex-wrap gap-4 text-sm text-muted-foreground">
            <LegendDot
              color={TOKEN_COLORS.input}
              label={t('todayTokens.vlmInput')}
            />
            <LegendDot
              color={TOKEN_COLORS.output}
              label={t('todayTokens.vlmOutput')}
            />
            <LegendDot
              color={TOKEN_COLORS.embedding}
              label={t('todayTokens.embeddingInput')}
            />
          </div>
        </>
      )}
    </Panel>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className="size-2.5 rounded-full"
        style={{ backgroundColor: color }}
      />
      <span>{label}</span>
    </span>
  )
}

function TokenTrendTooltip({
  active,
  label,
  payload,
  t,
}: {
  active?: boolean
  label?: string | number
  payload?: TokenTrendPayload[]
  t: HomeT
}) {
  if (!active || !payload?.length) return null

  const labelForKey = (key: string | undefined) => {
    if (key === 'vlm_input') return t('todayTokens.vlmInput')
    if (key === 'vlm_output') return t('todayTokens.vlmOutput')
    return t('todayTokens.embeddingInput')
  }

  return (
    <div className="min-w-56 rounded-xl border border-border/70 bg-popover/95 px-3.5 py-3 text-xs text-popover-foreground shadow-2xl shadow-black/10 ring-1 ring-foreground/5 backdrop-blur-md dark:shadow-black/35">
      <div className="font-medium tabular-nums text-foreground">
        {String(label ?? '')}
      </div>
      <div className="mt-3 space-y-2 border-t border-border/70 pt-3">
        {payload.map((item) => (
          <div
            key={item.dataKey ?? item.name}
            className="grid grid-cols-[auto_1fr_auto] items-center gap-2"
          >
            <span
              className="size-2 rounded-full"
              style={{ backgroundColor: item.color }}
            />
            <span className="min-w-0 truncate text-muted-foreground">
              {labelForKey(item.dataKey ?? item.name)}
            </span>
            <span className="font-medium tabular-nums text-foreground">
              {formatNumber(item.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
