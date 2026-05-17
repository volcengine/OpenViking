import { useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType, CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import HeatMap from '@uiw/react-heat-map'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  Coins,
  Database,
  Search,
  Users,
} from 'lucide-react'

import { Skeleton } from '#/components/ui/skeleton'
import { client } from '#/gen/ov-client/client.gen'
import { getOvResult } from '#/lib/ov-client'
import { cn } from '#/lib/utils'

type HomeT = (key: string, options?: Record<string, unknown>) => string

type ConsoleDashboardSummary = {
  agent_overview?: AgentOverview
  context_counts?: ContextCounts
  enabled?: boolean
  message?: string
  today_retrievals?: RetrievalCounts
  today_tokens?: TokenCounts
}

type ContextCounts = {
  files?: number
  memories?: number
  skills?: number
  total?: number
}

type TokenCounts = {
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

type RetrievalCounts = {
  find?: number
  search?: number
  total?: number
}

type AgentOverview = {
  items?: AgentVisit[]
  total?: number
}

type AgentVisit = {
  agent_id?: string
  last_seen_at?: string
}

type ConsoleSeries<TItem> = {
  bucket?: string
  enabled?: boolean
  end_date?: string
  items?: TItem[]
  message?: string
  start_date?: string
}

type TokenSeriesItem = {
  date?: string
  embedding_input?: number
  total?: number
  vlm_input?: number
  vlm_output?: number
}

type TokenTrendPayload = {
  color?: string
  dataKey?: string
  name?: string
  value?: number
}

type ContextCommitItem = {
  add_resource?: number
  add_skill?: number
  date?: string
  hour?: number
  session_add_message?: number
  session_commit?: number
  total?: number
}

type HeatMapDayValue = {
  count: number
  date: string
  details: Required<ContextCommitItem>
}

type CommitHeatmapStats = {
  activeDays: number
  peakCount: number
  peakDate: string
  recentDate: string
}

type CommitTooltip = {
  item: HeatMapDayValue
  x: number
  y: number
}

const TOKEN_SERIES_DAYS = 14
const COMMIT_SERIES_DAYS = 365

const TOKEN_COLORS = {
  embedding: 'oklch(0.5 0.11 252)',
  input: 'oklch(0.57 0.13 232)',
  output: 'oklch(0.62 0.12 188)',
}

const HOME_ACCENT_COLORS = {
  icon: 'oklch(0.68 0.14 232)',
  iconSoft: 'oklch(0.68 0.14 232 / 0.14)',
}

const HEATMAP_MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const HEATMAP_WEEK_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', '']
const HEATMAP_COLOR_STOPS = [
  'oklch(0.82 0.07 232)',
  'oklch(0.7 0.1 232)',
  'oklch(0.58 0.13 238)',
  'oklch(0.46 0.13 245)',
] as const
const HEATMAP_EMPTY_COLOR = 'oklch(0.92 0 0)'

function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function asNumber(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function formatNumber(value: unknown): string {
  return asNumber(value).toLocaleString()
}

function formatDateKey(date: Date): string {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function parseDateKey(value: string | undefined): Date {
  const fallback = new Date()
  if (!value) return fallback
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return fallback
  return new Date(year, month - 1, day)
}

function getLastDaysRange(days: number): { endDate: string; startDate: string } {
  const end = new Date()
  const start = new Date(end)
  start.setDate(end.getDate() - days + 1)
  return {
    endDate: formatDateKey(end),
    startDate: formatDateKey(start),
  }
}

function formatShortDate(value: string): string {
  if (!value) return '--'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })
}

function formatTimestamp(value: string): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
  })
}

function normalizeTokenSeries(items: unknown): Array<Required<TokenSeriesItem>> {
  return asArray(items).map((raw) => {
    const record = asRecord(raw)
    const vlmInput = asNumber(record.vlm_input)
    const vlmOutput = asNumber(record.vlm_output)
    const embeddingInput = asNumber(record.embedding_input)
    return {
      date: asString(record.date),
      embedding_input: embeddingInput,
      total: asNumber(record.total) || vlmInput + vlmOutput + embeddingInput,
      vlm_input: vlmInput,
      vlm_output: vlmOutput,
    }
  })
}

function percentile(sortedValues: number[], ratio: number): number {
  if (sortedValues.length === 0) return 0
  const index = Math.ceil(sortedValues.length * ratio) - 1
  return sortedValues[Math.max(0, Math.min(sortedValues.length - 1, index))]
}

function buildHeatmapPanelColors(items: HeatMapDayValue[]): Record<number, string> {
  const nonZeroCounts = Array.from(new Set(
    items
      .map((item) => item.count)
      .filter((count) => count > 0)
      .sort((a, b) => a - b),
  ))

  if (nonZeroCounts.length === 0) {
    return { 0: HEATMAP_EMPTY_COLOR }
  }

  const thresholds = Array.from(new Set([
    Math.max(1, percentile(nonZeroCounts, 0.25)),
    Math.max(1, percentile(nonZeroCounts, 0.5)),
    Math.max(1, percentile(nonZeroCounts, 0.75)),
    Math.max(1, percentile(nonZeroCounts, 0.9)),
  ])).sort((a, b) => a - b)

  return thresholds.reduce<Record<number, string>>(
    (colors, threshold, index) => ({
      ...colors,
      [threshold]: HEATMAP_COLOR_STOPS[Math.min(index, HEATMAP_COLOR_STOPS.length - 1)],
    }),
    { 0: HEATMAP_EMPTY_COLOR },
  )
}

function getHeatmapFillColor(count: number, panelColors: Record<number, string>): string {
  if (count <= 0) return 'var(--heatmap-empty)'

  const thresholds = Object.keys(panelColors)
    .map(Number)
    .filter((threshold) => threshold > 0)
    .sort((a, b) => a - b)

  const matched = thresholds.reduce<number | null>(
    (current, threshold) => (count >= threshold ? threshold : current),
    null,
  )

  return matched === null
    ? HEATMAP_COLOR_STOPS[0]
    : panelColors[matched] ?? HEATMAP_COLOR_STOPS[0]
}

function computeCommitHeatmapStats(items: HeatMapDayValue[]): CommitHeatmapStats {
  return items.reduce<CommitHeatmapStats>((stats, item) => {
    if (item.count <= 0) return stats

    return {
      activeDays: stats.activeDays + 1,
      peakCount: item.count > stats.peakCount ? item.count : stats.peakCount,
      peakDate: item.count > stats.peakCount ? item.date : stats.peakDate,
      recentDate: item.date > stats.recentDate ? item.date : stats.recentDate,
    }
  }, {
    activeDays: 0,
    peakCount: 0,
    peakDate: '',
    recentDate: '',
  })
}

function normalizeCommitHeatmapData(items: unknown): HeatMapDayValue[] {
  const rowsByDate = new Map<string, Required<ContextCommitItem>>()

  for (const item of normalizeCommitItems(items)) {
    if (!item.date) continue

    const existing = rowsByDate.get(item.date) ?? {
      add_resource: 0,
      add_skill: 0,
      date: item.date,
      hour: 0,
      session_add_message: 0,
      session_commit: 0,
      total: 0,
    }

    rowsByDate.set(item.date, {
      add_resource: existing.add_resource + item.add_resource,
      add_skill: existing.add_skill + item.add_skill,
      date: item.date,
      hour: 0,
      session_add_message: existing.session_add_message + item.session_add_message,
      session_commit: existing.session_commit + item.session_commit,
      total: existing.total + item.total,
    })
  }

  return Array.from(rowsByDate.values())
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((item) => ({
      count: item.total,
      date: item.date,
      details: item,
    }))
}

function normalizeCommitItems(items: unknown): Array<Required<ContextCommitItem>> {
  return asArray(items).map((raw) => {
    const record = asRecord(raw)
    const addResource = asNumber(record.add_resource)
    const addSkill = asNumber(record.add_skill)
    const sessionAddMessage = asNumber(record.session_add_message)
    const sessionCommit = asNumber(record.session_commit)
    return {
      add_resource: addResource,
      add_skill: addSkill,
      date: asString(record.date),
      hour: asNumber(record.hour),
      session_add_message: sessionAddMessage,
      session_commit: sessionCommit,
      total: asNumber(record.total) || addResource + addSkill + sessionAddMessage + sessionCommit,
    }
  })
}

function normalizeAgents(items: unknown): AgentVisit[] {
  return asArray(items)
    .map((raw) => {
      const record = asRecord(raw)
      return {
        agent_id: asString(record.agent_id),
        last_seen_at: asString(record.last_seen_at),
      }
    })
    .filter((item) => item.agent_id)
}

function isDisabledPayload(value: unknown): boolean {
  return asRecord(value).enabled === false
}

function fetchConsoleDashboardSummary(): Promise<ConsoleDashboardSummary> {
  return getOvResult<ConsoleDashboardSummary>(
    client.get({ url: '/api/v1/console/dashboard/summary' }),
  )
}

function fetchConsoleTokenSeries(): Promise<ConsoleSeries<TokenSeriesItem>> {
  const range = getLastDaysRange(TOKEN_SERIES_DAYS)
  return getOvResult<ConsoleSeries<TokenSeriesItem>>(
    client.get({
      query: {
        bucket: 'day',
        end_date: range.endDate,
        start_date: range.startDate,
      },
      url: '/api/v1/console/tokens',
    }),
  )
}

function fetchConsoleContextCommits(): Promise<ConsoleSeries<ContextCommitItem>> {
  const range = getLastDaysRange(COMMIT_SERIES_DAYS)
  return getOvResult<ConsoleSeries<ContextCommitItem>>(
    client.get({
      query: {
        bucket: '4h',
        end_date: range.endDate,
        start_date: range.startDate,
      },
      url: '/api/v1/console/context-commits',
    }),
  )
}

function Panel({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        'animate-home-panel-in rounded-2xl border border-border/70 bg-muted/80 p-6 shadow-sm transition-[background-color,border-color,box-shadow,transform] duration-200 ease-out hover:-translate-y-0.5 hover:border-border hover:bg-muted hover:shadow-md dark:border-white/10 dark:bg-white/[0.12] dark:hover:border-white/15 dark:hover:bg-white/[0.16]',
        className,
      )}
    >
      {children}
    </section>
  )
}

function SectionHeading({
  action,
  description,
  title,
}: {
  action?: ReactNode
  description?: string
  title: string
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 className="text-lg font-semibold tracking-normal">{title}</h2>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  )
}

function DetailRow({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="flex min-h-8 items-center justify-between gap-2 rounded-lg border border-[oklch(0.68_0.12_232/0.1)] bg-background/55 px-2.5 py-1.5 text-xs shadow-xs dark:border-white/10 dark:bg-white/[0.05]">
      <span className="min-w-0 truncate text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums text-[oklch(0.46_0.13_242)] dark:text-[oklch(0.74_0.12_232)]">{value}</span>
    </div>
  )
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-24 items-center justify-center rounded-xl border border-dashed border-border/60 bg-background/45 px-4 text-center text-sm text-muted-foreground dark:bg-white/[0.04]">
      {children}
    </div>
  )
}

function parseDisplayNumber(value: string): number | null {
  const normalized = value.replace(/,/g, '').trim()
  if (!normalized) return null
  const numeric = Number(normalized)
  return Number.isFinite(numeric) ? numeric : null
}

function easeOutCubic(value: number): number {
  return 1 - Math.pow(1 - value, 3)
}

function MetricPanel({
  children,
  description,
  icon: Icon,
  isError,
  isLoading,
  title,
  value,
}: {
  children?: ReactNode
  description: string
  icon: ComponentType<{ className?: string; style?: CSSProperties }>
  isError?: boolean
  isLoading?: boolean
  title: string
  value: string
}) {
  const valueRef = useRef<HTMLSpanElement>(null)
  const previousValueRef = useRef<string | null>(null)

  useEffect(() => {
    if (isLoading || isError) return
    const el = valueRef.current
    if (!el) return

    const target = parseDisplayNumber(value)
    if (target === null || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.textContent = value
      previousValueRef.current = value
      return
    }

    if (previousValueRef.current === value) {
      el.textContent = value
      return
    }

    const current = previousValueRef.current === null
      ? 0
      : parseDisplayNumber(previousValueRef.current) ?? target
    previousValueRef.current = value

    const startedAt = performance.now()
    const duration = 700
    let frame = 0

    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration)
      const next = current + (target - current) * easeOutCubic(progress)
      el.textContent = Math.round(next).toLocaleString()
      if (progress < 1) {
        frame = requestAnimationFrame(tick)
      } else {
        el.textContent = value
      }
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [isError, isLoading, value])

  return (
    <Panel className="flex min-h-[168px] flex-col p-4 sm:p-5">
      <div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold tracking-normal text-[oklch(0.42_0.04_232)] dark:text-[oklch(0.8_0.03_232)]">{title}</h2>
            <p className="sr-only">{description}</p>
          </div>
          <span
            className="flex size-7 shrink-0 items-center justify-center rounded-full"
            style={{ backgroundColor: HOME_ACCENT_COLORS.iconSoft }}
          >
            <Icon className="size-3.5" style={{ color: HOME_ACCENT_COLORS.icon }} />
          </span>
        </div>

        {isLoading ? (
          <Skeleton className="mt-4 h-10 w-24" />
        ) : isError ? (
          <p className="mt-4 text-sm text-destructive">{value}</p>
        ) : (
          <div className="mt-4 text-4xl font-bold leading-none tracking-normal tabular-nums text-foreground">
            <span ref={valueRef}>{value}</span>
          </div>
        )}
      </div>

      {children ? <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(92px,1fr))] gap-2">{children}</div> : null}
    </Panel>
  )
}

function ContextDataPanel({
  data,
  disabled,
  isError,
  isLoading,
  t,
}: {
  data: ContextCounts | undefined
  disabled: boolean
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const total = asNumber(data?.total)
  return (
    <MetricPanel
      description={t('contextData.description')}
      icon={Database}
      isError={isError}
      isLoading={isLoading}
      title={t('contextData.title')}
      value={isError ? t('requestFailed') : formatNumber(total)}
    >
      {disabled ? (
        <p className="text-xs text-muted-foreground">{t('usageDisabled')}</p>
      ) : (
        <>
          <DetailRow label={t('contextData.files')} value={formatNumber(data?.files)} />
          <DetailRow label={t('contextData.skills')} value={formatNumber(data?.skills)} />
          <DetailRow label={t('contextData.memories')} value={formatNumber(data?.memories)} />
        </>
      )}
    </MetricPanel>
  )
}

function TodayTokensPanel({
  data,
  disabled,
  isError,
  isLoading,
  t,
}: {
  data: TokenCounts | undefined
  disabled: boolean
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const total = asNumber(data?.total)
  return (
    <MetricPanel
      description={t('todayTokens.description')}
      icon={Coins}
      isError={isError}
      isLoading={isLoading}
      title={t('todayTokens.title')}
      value={isError ? t('requestFailed') : formatNumber(total)}
    >
      {disabled ? (
        <p className="text-xs text-muted-foreground">{t('usageDisabled')}</p>
      ) : (
        <>
          <DetailRow label={t('todayTokens.vlmInput')} value={formatNumber(data?.vlm_input)} />
          <DetailRow label={t('todayTokens.vlmOutput')} value={formatNumber(data?.vlm_output)} />
          <DetailRow label={t('todayTokens.embeddingInput')} value={formatNumber(data?.embedding_input)} />
        </>
      )}
    </MetricPanel>
  )
}

function TodayRetrievalsPanel({
  data,
  disabled,
  isError,
  isLoading,
  t,
}: {
  data: RetrievalCounts | undefined
  disabled: boolean
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const total = asNumber(data?.total)
  return (
    <MetricPanel
      description={t('todayRetrievals.description')}
      icon={Search}
      isError={isError}
      isLoading={isLoading}
      title={t('todayRetrievals.title')}
      value={isError ? t('requestFailed') : formatNumber(total)}
    >
      {disabled ? (
        <p className="text-xs text-muted-foreground">{t('usageDisabled')}</p>
      ) : (
        <>
          <DetailRow label="find()" value={formatNumber(data?.find)} />
          <DetailRow label="search()" value={formatNumber(data?.search)} />
        </>
      )}
    </MetricPanel>
  )
}

function AgentAccessPanel({
  data,
  disabled,
  isError,
  isLoading,
  t,
}: {
  data: AgentOverview | undefined
  disabled: boolean
  isError: boolean
  isLoading: boolean
  t: HomeT
}) {
  const agents = normalizeAgents(data?.items)
  const total = asNumber(data?.total)
  return (
    <MetricPanel
      description={t('agentAccess.description')}
      icon={Users}
      isError={isError}
      isLoading={isLoading}
      title={t('agentAccess.title')}
      value={isError ? t('requestFailed') : formatNumber(total)}
    >
      {disabled ? (
        <p className="text-xs text-muted-foreground">{t('usageDisabled')}</p>
      ) : agents.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('agentAccess.empty')}</p>
      ) : (
        <div className="grid gap-2">
          {agents.slice(0, 3).map((agent) => (
            <div
              key={agent.agent_id}
              className="flex min-h-8 items-center justify-between gap-2 rounded-lg border border-[oklch(0.68_0.12_232/0.1)] bg-background/55 px-2.5 py-1.5 text-xs shadow-xs dark:border-white/10 dark:bg-white/[0.05]"
            >
              <span className="min-w-0 truncate font-medium">{agent.agent_id}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatTimestamp(agent.last_seen_at || '')}
              </span>
            </div>
          ))}
        </div>
      )}
    </MetricPanel>
  )
}

function TokenTrendPanel({
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
  const rangeLabel = data?.start_date && data.end_date
    ? `${data.start_date} - ${data.end_date}`
    : `${getLastDaysRange(TOKEN_SERIES_DAYS).startDate} - ${getLastDaysRange(TOKEN_SERIES_DAYS).endDate}`

  return (
    <Panel>
      <SectionHeading
        action={<span className="rounded-full border border-[oklch(0.68_0.12_232/0.2)] bg-background/70 px-3 py-1 text-xs tabular-nums text-muted-foreground shadow-xs dark:bg-white/[0.06]">{rangeLabel}</span>}
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
              <AreaChart data={items} margin={{ bottom: 0, left: 0, right: 12, top: 8 }}>
                <defs>
                  <linearGradient id="tokenTrendVlmInput" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="5%" stopColor={TOKEN_COLORS.input} stopOpacity={0.52} />
                    <stop offset="95%" stopColor={TOKEN_COLORS.input} stopOpacity={0.14} />
                  </linearGradient>
                  <linearGradient id="tokenTrendVlmOutput" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="5%" stopColor={TOKEN_COLORS.output} stopOpacity={0.46} />
                    <stop offset="95%" stopColor={TOKEN_COLORS.output} stopOpacity={0.11} />
                  </linearGradient>
                  <linearGradient id="tokenTrendEmbedding" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="5%" stopColor={TOKEN_COLORS.embedding} stopOpacity={0.36} />
                    <stop offset="95%" stopColor={TOKEN_COLORS.embedding} stopOpacity={0.08} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="currentColor" strokeOpacity={0.08} vertical={false} />
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
                <Area dataKey="vlm_input" fill="url(#tokenTrendVlmInput)" name="vlm_input" stackId="tokens" stroke={TOKEN_COLORS.input} strokeWidth={2} type="monotone" />
                <Area dataKey="vlm_output" fill="url(#tokenTrendVlmOutput)" name="vlm_output" stackId="tokens" stroke={TOKEN_COLORS.output} strokeWidth={2} type="monotone" />
                <Area dataKey="embedding_input" fill="url(#tokenTrendEmbedding)" name="embedding_input" stackId="tokens" stroke={TOKEN_COLORS.embedding} strokeWidth={2} type="monotone" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-4 flex flex-wrap gap-4 text-sm text-muted-foreground">
            <LegendDot color={TOKEN_COLORS.input} label={t('todayTokens.vlmInput')} />
            <LegendDot color={TOKEN_COLORS.output} label={t('todayTokens.vlmOutput')} />
            <LegendDot color={TOKEN_COLORS.embedding} label={t('todayTokens.embeddingInput')} />
          </div>
        </>
      )}
    </Panel>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="size-2.5 rounded-full" style={{ backgroundColor: color }} />
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
      <div className="font-medium tabular-nums text-foreground">{String(label ?? '')}</div>
      <div className="mt-3 space-y-2 border-t border-border/70 pt-3">
        {payload.map((item) => (
          <div key={item.dataKey ?? item.name} className="grid grid-cols-[auto_1fr_auto] items-center gap-2">
            <span className="size-2 rounded-full" style={{ backgroundColor: item.color }} />
            <span className="min-w-0 truncate text-muted-foreground">{labelForKey(item.dataKey ?? item.name)}</span>
            <span className="font-medium tabular-nums text-foreground">{formatNumber(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ContextCommitStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-border/60 py-2 last:border-b-0 sm:border-b-0 sm:border-r sm:px-4 sm:last:border-r-0 xl:border-b xl:border-r-0 xl:px-0 xl:last:border-b-0">
      <div className="text-[11px] leading-none text-muted-foreground">{label}</div>
      <div className="mt-1.5 text-lg font-semibold leading-none tabular-nums">{value}</div>
    </div>
  )
}

function ContextCommitsPanel({
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
  const items = useMemo(() => normalizeCommitHeatmapData(data?.items), [data?.items])
  const panelColors = useMemo(() => buildHeatmapPanelColors(items), [items])
  const totalCommits = useMemo(() => items.reduce((total, item) => total + item.count, 0), [items])
  const stats = useMemo(() => computeCommitHeatmapStats(items), [items])
  const disabled = isDisabledPayload(data)
  const rangeLabel = data?.start_date && data.end_date
    ? `${data.start_date} - ${data.end_date}`
    : `${getLastDaysRange(COMMIT_SERIES_DAYS).startDate} - ${getLastDaysRange(COMMIT_SERIES_DAYS).endDate}`
  const range = getLastDaysRange(COMMIT_SERIES_DAYS)
  const startDate = parseDateKey(data?.start_date ?? range.startDate)
  const endDate = parseDateKey(data?.end_date ?? range.endDate)
  const title = !isLoading && !isError && !disabled
    ? totalCommits > 0
      ? t('contextCommits.yearlyTotal', { count: formatNumber(totalCommits) })
      : t('contextCommits.yearlyEmpty')
    : t('contextCommits.title')

  return (
    <Panel>
      <SectionHeading
        action={<span className="pt-1 text-xs tabular-nums text-muted-foreground">{rangeLabel}</span>}
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
                    const heatmapItem = value.details ? value as HeatMapDayValue : null
                    const count = asNumber(value.count)
                    const fill = getHeatmapFillColor(count, panelColors)
                    return (
                      <rect
                        {...props}
                        fill={fill}
                        onMouseEnter={(event) => {
                          if (!heatmapItem) return
                          const rect = (event.target as SVGRectElement).getBoundingClientRect()
                          setTooltip({
                            item: heatmapItem,
                            x: rect.left + rect.width / 2,
                            y: rect.top,
                          })
                        }}
                        onMouseLeave={() => setTooltip(null)}
                        style={{ ...props.style, cursor: heatmapItem ? 'pointer' : 'default', fill, transition: 'fill 0.15s, opacity 0.15s' }}
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
                  <span className="mr-0.5">{t('contextCommits.legend.none')}</span>
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
                  <span className="ml-0.5">{t('contextCommits.legend.more')}</span>
                </div>
              </div>
            </div>

            <div className="grid content-start border-t border-border/60 pt-3 sm:grid-cols-3 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-5">
              <ContextCommitStat label={t('contextCommits.stats.activeDays')} value={formatNumber(stats.activeDays)} />
              <ContextCommitStat label={t('contextCommits.stats.peakDay')} value={formatNumber(stats.peakCount)} />
              <ContextCommitStat label={t('contextCommits.stats.recentDay')} value={stats.recentDate ? formatShortDate(stats.recentDate) : '--'} />
            </div>
          </div>
        </>
      )}

      {tooltip && typeof document !== 'undefined'
        ? createPortal(<CommitTooltipView item={tooltip.item} t={t} x={tooltip.x} y={tooltip.y} />, document.body)
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
    { label: t('contextCommits.operations.addResource'), value: details.add_resource },
    { label: t('contextCommits.operations.addSkill'), value: details.add_skill },
    { label: t('contextCommits.operations.sessionAddMessage'), value: details.session_add_message },
    { label: t('contextCommits.operations.sessionCommit'), value: details.session_commit },
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
          <div className="mt-0.5 text-[11px] text-muted-foreground">{t('contextCommits.tooltip.total')}</div>
        </div>
        <div className="rounded-md bg-[oklch(0.68_0.12_232_/_0.14)] px-2 py-1 text-sm font-semibold tabular-nums text-[oklch(0.45_0.13_242)] dark:bg-[oklch(0.68_0.14_232_/_0.18)] dark:text-[oklch(0.76_0.14_232)]">
          {details.total}
        </div>
      </div>

      <div className="mt-3 space-y-2 border-t border-border/70 pt-3">
        {rows.map((row, index) => (
          <div key={row.label} className="grid grid-cols-[auto_1fr_auto] items-center gap-2">
            <span
              className="size-1.5 rounded-full"
              style={{
                backgroundColor: HEATMAP_COLOR_STOPS[Math.min(index, HEATMAP_COLOR_STOPS.length - 1)],
                opacity: row.value > 0 ? 1 : 0.35,
              }}
            />
            <span className="min-w-0 truncate text-muted-foreground">{row.label}</span>
            <span className="font-medium tabular-nums">{formatNumber(row.value)}</span>
          </div>
        ))}
      </div>

      <span className="absolute left-1/2 top-full size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-border/70 bg-popover/95" />
    </div>
  )
}

export function HomePage() {
  const { t } = useTranslation('home')

  const dashboard = useQuery({
    queryFn: fetchConsoleDashboardSummary,
    queryKey: ['console-dashboard-summary'],
    refetchInterval: 30_000,
  })

  const tokenSeries = useQuery({
    queryFn: fetchConsoleTokenSeries,
    queryKey: ['console-token-series', 'last-14-days'],
    refetchInterval: 60_000,
  })

  const contextCommits = useQuery({
    queryFn: fetchConsoleContextCommits,
    queryKey: ['console-context-commits', 'last-365-days'],
    refetchInterval: 60_000,
  })

  const summary = dashboard.data
  const usageDisabled = isDisabledPayload(summary)

  return (
    <div className="flex flex-col gap-5 pb-8">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <ContextDataPanel
          data={summary?.context_counts}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
        <TodayTokensPanel
          data={summary?.today_tokens}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
        <TodayRetrievalsPanel
          data={summary?.today_retrievals}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
        <AgentAccessPanel
          data={summary?.agent_overview}
          disabled={usageDisabled}
          isError={dashboard.isError}
          isLoading={dashboard.isLoading}
          t={t}
        />
      </div>

      <TokenTrendPanel
        data={tokenSeries.data}
        isError={tokenSeries.isError}
        isLoading={tokenSeries.isLoading}
        t={t}
      />

      <ContextCommitsPanel
        data={contextCommits.data}
        isError={contextCommits.isError}
        isLoading={contextCommits.isLoading}
        t={t}
      />
    </div>
  )
}
