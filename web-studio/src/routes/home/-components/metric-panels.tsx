import { useEffect, useRef } from 'react'
import type { ComponentType, CSSProperties, ReactNode } from 'react'
import { Coins, Database, Search, Users } from 'lucide-react'

import { Skeleton } from '#/components/ui/skeleton'

import { HOME_ACCENT_COLORS } from '../-constants/dashboard'
import type {
  AgentOverview,
  ContextCounts,
  HomeT,
  RetrievalCounts,
  TokenCounts,
} from '../-types/dashboard'
import { asNumber, formatNumber, formatTimestamp } from '../-lib/format'
import { normalizeAgents } from '../-lib/normalize'
import { DetailRow, Panel } from './panel'

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
    if (
      target === null ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      el.textContent = value
      previousValueRef.current = value
      return
    }

    if (previousValueRef.current === value) {
      el.textContent = value
      return
    }

    const current =
      previousValueRef.current === null
        ? 0
        : (parseDisplayNumber(previousValueRef.current) ?? target)
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
            <h2 className="truncate text-sm font-semibold tracking-normal text-[oklch(0.42_0.04_232)] dark:text-[oklch(0.8_0.03_232)]">
              {title}
            </h2>
            <p className="sr-only">{description}</p>
          </div>
          <span
            className="flex size-7 shrink-0 items-center justify-center rounded-full"
            style={{ backgroundColor: HOME_ACCENT_COLORS.iconSoft }}
          >
            <Icon
              className="size-3.5"
              style={{ color: HOME_ACCENT_COLORS.icon }}
            />
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

      {children ? (
        <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(92px,1fr))] gap-2">
          {children}
        </div>
      ) : null}
    </Panel>
  )
}

export function ContextDataPanel({
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
          <DetailRow
            label={t('contextData.files')}
            value={formatNumber(data?.files)}
          />
          <DetailRow
            label={t('contextData.skills')}
            value={formatNumber(data?.skills)}
          />
          <DetailRow
            label={t('contextData.memories')}
            value={formatNumber(data?.memories)}
          />
        </>
      )}
    </MetricPanel>
  )
}

export function TodayTokensPanel({
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
          <DetailRow
            label={t('todayTokens.vlmInput')}
            value={formatNumber(data?.vlm_input)}
          />
          <DetailRow
            label={t('todayTokens.vlmOutput')}
            value={formatNumber(data?.vlm_output)}
          />
          <DetailRow
            label={t('todayTokens.embeddingInput')}
            value={formatNumber(data?.embedding_input)}
          />
        </>
      )}
    </MetricPanel>
  )
}

export function TodayRetrievalsPanel({
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
          <DetailRow
            label={t('todayRetrievals.find')}
            value={formatNumber(data?.find)}
          />
          <DetailRow
            label={t('todayRetrievals.search')}
            value={formatNumber(data?.search)}
          />
        </>
      )}
    </MetricPanel>
  )
}

export function AgentAccessPanel({
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
        <p className="text-xs text-muted-foreground">
          {t('agentAccess.empty')}
        </p>
      ) : (
        <div className="grid gap-2">
          {agents.slice(0, 3).map((agent) => (
            <div
              key={agent.agent_id}
              className="flex min-h-8 items-center justify-between gap-2 rounded-lg border border-[oklch(0.68_0.12_232/0.1)] bg-background/55 px-2.5 py-1.5 text-xs shadow-xs dark:border-white/10 dark:bg-white/[0.05]"
            >
              <span className="min-w-0 truncate font-medium">
                {agent.agent_id}
              </span>
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
