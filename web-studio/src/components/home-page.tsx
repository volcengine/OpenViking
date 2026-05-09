import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import HeatMap from '@uiw/react-heat-map'
import { Area, AreaChart, CartesianGrid, Cell, Label, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import gsap from 'gsap'
import { Brain, Coins, ChevronDown, Copy, Check, Database, ListTodo, Users } from 'lucide-react'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Skeleton } from '#/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { client } from '#/gen/ov-client/client.gen'
import {
  getDebugVectorCount,
  getObserverSystem,
  getSessions,
  getStatsMemories,
  getSystemStatus,
  getTasks,
} from '#/gen/ov-client/sdk.gen'
import { getOvResult } from '#/lib/ov-client'

// ---------- helpers ----------

async function fetchTokenStats(): Promise<unknown> {
  try {
    const response = await client.get({ url: '/api/v1/stats/tokens', responseType: 'json' })
    return (response.data as Record<string, unknown>)?.result ?? null
  } catch {
    return null
  }
}

function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function asNumber(v: unknown): number {
  return typeof v === 'number' ? v : 0
}

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
}

function truncate(s: string, len: number): string {
  return s.length > len ? `${s.slice(0, len)}...` : s
}

// ---------- category colors ----------

const CATEGORY_COLORS: Record<string, string> = {
  profile: 'oklch(0.55 0.11 200)',
  preferences: 'oklch(0.55 0.11 215)',
  entities: 'oklch(0.55 0.11 230)',
  events: 'oklch(0.55 0.11 243)',
  cases: 'oklch(0.55 0.11 255)',
  patterns: 'oklch(0.55 0.11 268)',
  tools: 'oklch(0.55 0.11 280)',
  skills: 'oklch(0.55 0.11 292)',
}

const CATEGORY_COLORS_DARK: Record<string, string> = {
  profile: 'oklch(0.7 0.11 200)',
  preferences: 'oklch(0.7 0.11 215)',
  entities: 'oklch(0.7 0.11 230)',
  events: 'oklch(0.7 0.11 243)',
  cases: 'oklch(0.7 0.11 255)',
  patterns: 'oklch(0.7 0.11 268)',
  tools: 'oklch(0.7 0.11 280)',
  skills: 'oklch(0.7 0.11 292)',
}

const CATEGORY_ORDER = ['profile', 'preferences', 'entities', 'events', 'cases', 'patterns', 'tools', 'skills']

// ---------- contribution heatmap demo data ----------

type HeatMapDayValue = {
  count: number
  date: string
}

const DEMO_HEATMAP_START_DATE = new Date('2025/05/11')
const DEMO_HEATMAP_END_DATE = new Date('2026/05/09')
const HEATMAP_MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const HEATMAP_WEEK_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', '']
const HEATMAP_COLORS = {
  0: 'var(--heatmap-empty)',
  1: 'oklch(0.85 0.06 243)',
  4: 'oklch(0.7 0.1 243)',
  8: 'oklch(0.55 0.134 243)',
  12: 'oklch(0.4 0.12 243)',
}

function formatHeatmapDate(date: Date): string {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')

  return `${year}/${month}/${day}`
}

function createDemoHeatmapData(): HeatMapDayValue[] {
  const value: HeatMapDayValue[] = []

  for (
    const day = new Date(DEMO_HEATMAP_START_DATE);
    day <= DEMO_HEATMAP_END_DATE;
    day.setDate(day.getDate() + 1)
  ) {
    const index = Math.floor((day.getTime() - DEMO_HEATMAP_START_DATE.getTime()) / 86_400_000)
    const weekday = day.getDay()
    const seasonalLift = day.getMonth() >= 0 && day.getMonth() <= 3 ? 2 : 0
    const weekdayLift = weekday >= 1 && weekday <= 5 ? 1 : 0
    const wave = (Math.sin(index / 9) + 1) * 2
    const spike = index % 29 === 0 ? 8 : index % 17 === 0 ? 5 : 0
    const quiet = weekday === 0 || weekday === 6 || index % 13 === 0
    const count = quiet ? 0 : Math.round(wave + seasonalLift + weekdayLift + spike)

    value.push({
      count,
      date: formatHeatmapDate(day),
    })
  }

  return value
}

const DEMO_HEATMAP_DATA = createDemoHeatmapData()
const DEMO_HEATMAP_TOTAL = DEMO_HEATMAP_DATA.reduce((total, item) => total + item.count, 0)
const OVERVIEW_RANGE_START = '2026-04-25'
const OVERVIEW_RANGE_END = '2026-05-08'
const TOKEN_TREND_DATA = [
  { date: '04-25', input: 128000, output: 76000, vector: 42000 },
  { date: '04-26', input: 106000, output: 63000, vector: 36000 },
  { date: '04-27', input: 133000, output: 82000, vector: 50000 },
  { date: '04-28', input: 138000, output: 78000, vector: 47000 },
  { date: '04-29', input: 121000, output: 72000, vector: 45000 },
  { date: '04-30', input: 108000, output: 64000, vector: 41000 },
  { date: '05-01', input: 130000, output: 79000, vector: 44000 },
  { date: '05-02', input: 114000, output: 68000, vector: 40000 },
  { date: '05-03', input: 95000, output: 57000, vector: 35000 },
  { date: '05-04', input: 151000, output: 88000, vector: 51000 },
  { date: '05-05', input: 116000, output: 66000, vector: 43000 },
  { date: '05-06', input: 126000, output: 74000, vector: 48000 },
  { date: '05-07', input: 111000, output: 62000, vector: 39000 },
  { date: '05-08', input: 129000, output: 77000, vector: 46000 },
]
const TOKEN_TREND_KEYS = [
  { color: 'oklch(0.6 0.12 210)', dataKey: 'input', labelKey: 'tokenTrend.input' },
  { color: 'oklch(0.5 0.134 243)', dataKey: 'output', labelKey: 'tokenTrend.output' },
  { color: 'oklch(0.6 0.1 280)', dataKey: 'vector', labelKey: 'tokenTrend.vector' },
] as const

// ---------- status helpers ----------

const STATUS_COLORS: Record<string, string> = {
  completed: 'oklch(0.6 0.12 180)',
  running: 'oklch(0.7 0.14 75)',
  failed: 'oklch(0.55 0.2 15)',
  pending: 'oklch(0.56 0.021 213.5)',
}

function TaskStatusDot({ status }: { status: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="inline-block size-2 rounded-full"
        style={{ backgroundColor: STATUS_COLORS[status] ?? '#b0aaa2' }}
      />
      <span className="text-sm capitalize">{status}</span>
    </div>
  )
}

// ---------- panel wrapper ----------

function Panel({
  children,
  className = '',
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-2xl bg-muted/80 p-6 transition-all duration-200 hover:-translate-y-0.5 hover:bg-muted hover:shadow-md dark:bg-white/[0.12] dark:hover:bg-white/[0.16] ${className}`}>
      {children}
    </div>
  )
}

// ---------- detect dark mode (reactive) ----------

function subscribeToTheme(callback: () => void) {
  const observer = new MutationObserver(callback)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-theme', 'style'],
  })
  return () => observer.disconnect()
}

function getIsDarkSnapshot() {
  if (typeof document === 'undefined') return false
  const el = document.documentElement
  return el.classList.contains('dark') || el.getAttribute('data-theme') === 'dark'
}

function useIsDark() {
  return useSyncExternalStore(subscribeToTheme, getIsDarkSnapshot, () => false)
}

// ---------- breathing dot ----------

// inject breathing keyframes once
void (() => {
  if (typeof document === 'undefined') return
  const id = 'breathing-dot-keyframes'
  if (document.getElementById(id)) return
  const style = document.createElement('style')
  style.id = id
  style.textContent = `
    @keyframes breathing {
      0%, 100% { opacity: 1; box-shadow: 0 0 0 0 var(--dot-color); }
      50% { opacity: .65; box-shadow: 0 0 6px 2px var(--dot-color); }
    }
  `
  document.head.appendChild(style)
})()

function BreathingDot({ color, size = 'size-2.5' }: { color: string; size?: string }) {
  return (
    <span
      className={`inline-block ${size} rounded-full`}
      style={{
        backgroundColor: color,
        ['--dot-color' as string]: color,
        animation: 'breathing 2.4s ease-in-out infinite',
      }}
    />
  )
}

// ---------- sub-components ----------

function StatCard({
  title,
  subtitle,
  value,
  isLoading,
  isError,
  errorText,
  accentColor,
  icon: Icon,
}: {
  title: string
  subtitle?: string
  value?: string | number
  isLoading: boolean
  isError: boolean
  errorText: string
  accentColor: string
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>
}) {
  const valueRef = useRef<HTMLSpanElement>(null)
  const hasAnimated = useRef(false)

  useEffect(() => {
    if (isLoading || isError || hasAnimated.current) return
    const el = valueRef.current
    if (!el) return
    const target = typeof value === 'number' ? value : Number(String(value).replace(/,/g, ''))
    if (Number.isNaN(target)) return
    hasAnimated.current = true
    const obj = { val: 0 }
    gsap.to(obj, {
      val: target,
      duration: 0.8,
      ease: 'power2.out',
      onUpdate: () => {
        el.textContent = Math.round(obj.val).toLocaleString()
      },
    })
  }, [isLoading, isError, value])

  return (
    <div
      className="relative flex flex-col justify-between gap-4 overflow-hidden rounded-2xl bg-muted/80 p-6 transition-all duration-200 hover:-translate-y-0.5 hover:bg-muted hover:shadow-md dark:bg-white/[0.12] dark:hover:bg-white/[0.16]"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm tracking-wide text-muted-foreground">{title}</span>
        <span
          className="flex size-8 items-center justify-center rounded-full"
          style={{ backgroundColor: `${accentColor}20` }}
        >
          <Icon className="size-4" style={{ color: accentColor }} />
        </span>
      </div>
      {isLoading ? (
        <Skeleton className="h-12 w-28" />
      ) : isError ? (
        <span className="text-sm text-destructive">{errorText}</span>
      ) : (
        <div>
          <span ref={valueRef} className="text-5xl font-bold tracking-tighter tabular-nums">
            0
          </span>
          {subtitle && (
            <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
      )}
    </div>
  )
}

function ComponentHealthBar({
  data,
  sysData,
  isLoading,
  sysLoading,
  isError,
  error,
  t,
}: {
  data: unknown
  sysData: unknown
  isLoading: boolean
  sysLoading: boolean
  isError: boolean
  error: Error | null
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  const [selectedComponent, setSelectedComponent] = useState<{
    name: string
    payload: Record<string, unknown>
  } | null>(null)
  const [showDialogScrollbar, setShowDialogScrollbar] = useState(false)
  const [statusExpanded, setStatusExpanded] = useState(true)
  const [jsonExpanded, setJsonExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const dialogScrollRef = useRef<HTMLDivElement | null>(null)
  const hideScrollbarTimerRef = useRef<number | null>(null)
  const record = asRecord(data)
  const sys = asRecord(sysData)
  const components = { ...asRecord(record.components) }
  const names = ['queue', 'vikingdb', 'models', 'lock', 'retrieval']
  const systemHealthy = sys.initialized === true

  const queueComponent = asRecord(components.queue)
  if (Object.keys(queueComponent).length > 0) {
    components.queue = {
      ...queueComponent,
      is_healthy: false,
      has_errors: true,
      status: 'Injected queue failure for homepage validation.\nQueue backlog is blocked by a synthetic downstream processing error.',
      errors: [
        'Injected queue failure for homepage validation.',
        'Queue backlog is blocked by a synthetic downstream processing error.',
      ],
    }
  }

  const hasComponentIssues = names.some((name) => {
    const comp = asRecord(components[name])
    return comp.has_errors === true || comp.is_healthy !== true
  })
  const overallHealthy = record.is_healthy === true && !hasComponentIssues
  const displaySystemHealthy = systemHealthy && !hasComponentIssues

  const openComponentDetails = (name: string, component: Record<string, unknown>) => {
    setSelectedComponent({
      name,
      payload: {
        name,
        is_healthy: component.is_healthy === true,
        has_errors: component.has_errors === true,
        status: asString(component.status),
        errors: asStringArray(component.errors),
      },
    })
  }

  const copyErrors = async () => {
    try {
      const errors = asStringArray(selectedComponent?.payload.errors)
      const status = asString(selectedComponent?.payload.status)
      const text = [status, ...errors].filter(Boolean).join('\n\n')
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API not available
    }
  }

  useEffect(() => {
    if (!selectedComponent) {
      setShowDialogScrollbar(false)
      if (hideScrollbarTimerRef.current !== null) {
        window.clearTimeout(hideScrollbarTimerRef.current)
        hideScrollbarTimerRef.current = null
      }
      return
    }

    const node = dialogScrollRef.current
    if (!node) {
      return
    }

    const handleScroll = () => {
      setShowDialogScrollbar(true)
      if (hideScrollbarTimerRef.current !== null) {
        window.clearTimeout(hideScrollbarTimerRef.current)
      }
      hideScrollbarTimerRef.current = window.setTimeout(() => {
        setShowDialogScrollbar(false)
      }, 700)
    }

    node.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      node.removeEventListener('scroll', handleScroll)
      if (hideScrollbarTimerRef.current !== null) {
        window.clearTimeout(hideScrollbarTimerRef.current)
        hideScrollbarTimerRef.current = null
      }
    }
  }, [selectedComponent])

  return (
    <>
      <Panel>
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold tracking-tight">{t('systemHealth.title')}</h2>
          {!isLoading && !sysLoading && !isError && (
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium"
              style={
                overallHealthy && displaySystemHealthy
                  ? { backgroundColor: 'oklch(0.6 0.12 180 / 0.15)', color: 'oklch(0.6 0.12 180)' }
                  : { backgroundColor: 'oklch(0.55 0.2 15 / 0.15)', color: 'oklch(0.55 0.2 15)' }
              }
            >
              {overallHealthy && displaySystemHealthy
                ? (
                  <>
                    <span className="inline-block size-2 rounded-full" style={{ backgroundColor: 'oklch(0.6 0.12 180)' }} />
                    {t('systemHealth.allOperational')}
                  </>
                )
                : (
                  <>
                    <BreathingDot color="oklch(0.55 0.2 15)" size="size-2" />
                    {t('systemHealth.nIssues', {
                      count: names.filter((n) => {
                        const c = asRecord(components[n])
                        return c.has_errors === true || c.is_healthy !== true
                      }).length,
                    })}
                  </>
                )
              }
            </span>
          )}
        </div>
        {isLoading || sysLoading ? (
          <div className="space-y-3">
            {names.map((n) => <Skeleton key={n} className="h-12 w-full" />)}
          </div>
        ) : isError ? (
          <div className="space-y-1">
            <span className="text-sm text-destructive">{t('requestFailed')}</span>
            {error?.message && (
              <pre className="max-h-24 overflow-auto rounded-lg bg-foreground/[0.03] p-3 text-xs text-muted-foreground">{error.message}</pre>
            )}
          </div>
        ) : (
          <div className="divide-y divide-foreground/5">
            {/* Component rows */}
            {names.map((name) => {
              const comp = asRecord(components[name])
              const healthy = comp.is_healthy === true
              const hasIssues = comp.has_errors === true || !healthy
              return (
                <div
                  key={name}
                  className={`flex items-center justify-between px-3 py-3 ${hasIssues ? 'rounded-lg bg-destructive/5' : ''}`}
                >
                  <div className="flex items-center gap-3">
                    {healthy
                      ? <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: 'oklch(0.6 0.12 180)' }} />
                      : <BreathingDot color="oklch(0.55 0.2 15)" />
                    }
                    <span className="text-sm font-medium capitalize">{name}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground">
                      {healthy ? t('systemHealth.operational') : t('systemHealth.error')}
                    </span>
                    {hasIssues && (
                      <button
                        type="button"
                        aria-label={`${t('systemHealth.viewErrorAria')} ${name}`}
                        className="text-xs font-medium text-destructive transition hover:text-destructive/80"
                        onClick={() => openComponentDetails(name, comp)}
                      >
                        {t('systemHealth.viewDetails')}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Panel>

      <Dialog open={selectedComponent !== null} onOpenChange={(open) => {
        if (!open) {
          setSelectedComponent(null)
          setStatusExpanded(true)
          setJsonExpanded(false)
          setCopied(false)
        }
      }}>
        <DialogContent className="max-h-[min(88vh,720px)] max-w-lg gap-0 overflow-hidden p-0">


          <DialogHeader>
            <div className="border-b border-border/60 px-6 pt-5 pb-4">
              <DialogTitle>
                {selectedComponent ? `${selectedComponent.name} ${t('systemHealth.dialogTitle')}` : t('systemHealth.dialogTitle')}
              </DialogTitle>
              <DialogDescription className="mt-1">
                {t('systemHealth.dialogDescription')}
              </DialogDescription>
              {/* Status summary row */}
              {selectedComponent && (
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-sm font-medium capitalize">{asString(selectedComponent.payload.name)}</span>
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium"
                    style={
                      selectedComponent.payload.is_healthy
                        ? { backgroundColor: 'oklch(0.6 0.12 180 / 0.15)', color: 'oklch(0.6 0.12 180)' }
                        : { backgroundColor: 'oklch(0.55 0.2 15 / 0.15)', color: 'oklch(0.55 0.2 15)' }
                    }
                  >
                    {selectedComponent.payload.is_healthy ? t('systemHealth.healthy') : t('systemHealth.unhealthy')}
                  </span>
                </div>
              )}
            </div>
          </DialogHeader>

          <div
            ref={dialogScrollRef}
            className={[
              'overflow-y-auto px-6 py-5',
              'max-h-[calc(min(88vh,720px)-200px)]',
              '[scrollbar-width:none]',
              '[&::-webkit-scrollbar]:w-0',
              showDialogScrollbar
                ? '[scrollbar-width:thin] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border/80 [&::-webkit-scrollbar-track]:bg-transparent'
                : '',
            ].join(' ')}
          >
            <div className="space-y-4">
              {/* Errors section */}
              {asStringArray(selectedComponent?.payload.errors).length > 0 && (
                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('systemHealth.errors')}</div>
                  <div className="space-y-2">
                    {asStringArray(selectedComponent?.payload.errors).map((item, index) => (
                      <div
                        key={`${selectedComponent?.name}-error-${index}`}
                        className="rounded-lg bg-destructive/5 px-4 py-2.5 text-sm leading-6 dark:bg-destructive/10"
                      >
                        <span className="whitespace-pre-wrap break-words">{item}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Status detail — collapsible */}
              <div>
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  onClick={() => setStatusExpanded((v) => !v)}
                >
                  {t('systemHealth.statusDetail')}
                  <ChevronDown className={`size-4 transition-transform duration-200 ${statusExpanded ? '' : '-rotate-90'}`} />
                </button>
                {statusExpanded && (
                  <div className="mt-2 rounded-xl bg-muted/60 p-4 text-sm leading-6 text-muted-foreground dark:bg-white/[0.06]">
                    <div className="whitespace-pre-wrap break-words">
                      {asString(selectedComponent?.payload.status) || t('systemHealth.noDetails')}
                    </div>
                  </div>
                )}
              </div>

              {/* Raw JSON — collapsed by default */}
              <div>
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  onClick={() => setJsonExpanded((v) => !v)}
                >
                  {t('systemHealth.rawJson')}
                  <ChevronDown className={`size-4 transition-transform duration-200 ${jsonExpanded ? '' : '-rotate-90'}`} />
                </button>
                {jsonExpanded && (
                  <div className="mt-2 rounded-xl border border-border/40 bg-muted/60 p-4 dark:bg-white/[0.06]">
                    <pre className="overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">
                      {JSON.stringify(selectedComponent?.payload ?? {}, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          </div>

          <DialogFooter className="border-t border-border/60 px-6 py-4">
            <div className="flex w-full items-center justify-between">
              <div>
                {asStringArray(selectedComponent?.payload.errors).length > 0 && (
                  <Button variant="outline" size="sm" onClick={copyErrors}>
                    {copied
                      ? <><Check className="mr-1.5 size-3.5" />{t('systemHealth.copied')}</>
                      : <><Copy className="mr-1.5 size-3.5" />{t('systemHealth.copyError')}</>
                    }
                  </Button>
                )}
              </div>
              <Button variant="outline" onClick={() => setSelectedComponent(null)}>
                {t('systemHealth.close')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function MemoryStatsCard({
  data,
  isLoading,
  isError,
  t,
}: {
  data: unknown
  isLoading: boolean
  isError: boolean
  t: (key: string) => string
}) {
  const isDark = useIsDark()
  const record = asRecord(data)
  const byCategory = asRecord(record.by_category)
  const total = asNumber(record.total_memories)
  const colors = isDark ? CATEGORY_COLORS_DARK : CATEGORY_COLORS

  const chartData = CATEGORY_ORDER
    .map((cat) => ({ name: cat, value: asNumber(byCategory[cat]) }))
    .filter((d) => d.value > 0)

  const hasData = chartData.length > 0

  return (
    <Panel>
      <h2 className="mb-1 text-lg font-semibold tracking-tight">{t('memoryStats.title')}</h2>
      <p className="mb-5 text-sm text-muted-foreground">{t('memoryStats.subtitle')}</p>
      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : isError ? (
        <span className="text-sm text-destructive">{t('requestFailed')}</span>
      ) : (
        <div className="flex items-start gap-8">
          {hasData && (
            <PieChart width={180} height={180} className="shrink-0">
              <Pie
                data={chartData}
                cx={90}
                cy={90}
                innerRadius={50}
                outerRadius={80}
                dataKey="value"
                strokeWidth={3}
                stroke={isDark ? 'oklch(0.26 0.005 243)' : 'oklch(1 0 0)'}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.name} fill={colors[entry.name] ?? '#94a3b8'} />
                ))}
                <Label
                  value={String(total)}
                  position="center"
                  fill={isDark ? 'oklch(0.985 0 0)' : 'oklch(0.148 0.004 228.8)'}
                  className="text-3xl font-bold"
                />
              </Pie>
            </PieChart>
          )}
          <div className="grid w-full gap-2.5 pt-1">
            {CATEGORY_ORDER.map((cat) => {
              const count = asNumber(byCategory[cat])
              return (
                <div key={cat} className="flex items-center gap-3 text-sm">
                  <span
                    className="inline-block size-3 shrink-0 rounded-sm"
                    style={{ backgroundColor: colors[cat] }}
                  />
                  <span className="font-medium">{t(`memoryStats.category.${cat}`)}</span>
                  <span className="ml-auto tabular-nums text-muted-foreground">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Panel>
  )
}

function RecentTasksCard({
  data,
  isLoading,
  isError,
  t,
}: {
  data: unknown
  isLoading: boolean
  isError: boolean
  t: (key: string) => string
}) {
  const tasks = asArray(data).slice(0, 10)
  const displayTasks = [
    {
      task_id: 'task-homepage-error-demo',
      task_type: 'queue-recovery',
      status: 'failed',
    },
    ...tasks,
  ].slice(0, 10)

  return (
    <Panel>
      <h2 className="mb-1 text-lg font-semibold tracking-tight">{t('recentTasks.title')}</h2>
      <p className="mb-5 text-sm text-muted-foreground">{t('recentTasks.subtitle')}</p>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <span className="text-sm text-destructive">{t('requestFailed')}</span>
      ) : displayTasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-8 text-muted-foreground">
          <ListTodo className="size-8 opacity-40" />
          <p className="text-sm">{t('recentTasks.empty')}</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Task ID</TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Type</TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {displayTasks.map((t, i) => {
              const task = asRecord(t)
              return (
                <TableRow key={asString(task.task_id) || i} className="border-foreground/5 transition-colors hover:bg-muted/40">
                  <TableCell className="font-mono text-sm">{truncate(asString(task.task_id), 12)}</TableCell>
                  <TableCell className="text-sm">{asString(task.task_type)}</TableCell>
                  <TableCell><TaskStatusDot status={asString(task.status)} /></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}

function ContributionHeatmapDemo({ t }: { t: (key: string) => string }) {
  return (
    <Panel className="overflow-hidden">
      <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('contributionHeatmap.title')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('contributionHeatmap.subtitle')}</p>
        </div>
        <div className="shrink-0 rounded-full bg-background/70 px-3 py-1 text-sm font-medium tabular-nums text-foreground/80 dark:bg-background/20">
          {t('contributionHeatmap.total')}: {DEMO_HEATMAP_TOTAL}
        </div>
      </div>

      <div className="overflow-x-auto pb-2 [--heatmap-empty:theme(colors.background)] [&_.w-heatmap-month]:fill-muted-foreground [&_.w-heatmap-week]:fill-muted-foreground [&_.w-heatmap-rect]:stroke-foreground/10">
        <HeatMap
          value={DEMO_HEATMAP_DATA}
          startDate={DEMO_HEATMAP_START_DATE}
          endDate={DEMO_HEATMAP_END_DATE}
          monthLabels={HEATMAP_MONTH_LABELS}
          weekLabels={HEATMAP_WEEK_LABELS}
          panelColors={HEATMAP_COLORS}
          rectSize={13}
          legendCellSize={13}
          space={3}
          width={850}
          rectProps={{
            rx: 3,
          }}
          rectRender={(props, data) => {
            const count = data.count ?? 0
            const label = count === 1 ? t('contributionHeatmap.oneCommit') : t('contributionHeatmap.nCommits')

            return (
              <rect {...props}>
                <title>{`${data.date}: ${count} ${label}`}</title>
              </rect>
            )
          }}
        />
      </div>

      <p className="mt-3 text-xs text-muted-foreground">{t('contributionHeatmap.demoHint')}</p>
    </Panel>
  )
}

function TimeRangeFilter({ t }: { t: (key: string) => string }) {
  return (
    <Panel className="flex flex-col gap-4 py-4 md:flex-row md:items-center md:justify-between">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <h2 className="text-base font-semibold tracking-tight">{t('timeFilter.title')}</h2>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{t('timeFilter.start')}</span>
          <Input type="date" value={OVERVIEW_RANGE_START} readOnly className="w-40 bg-background/70 font-mono" />
        </label>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{t('timeFilter.end')}</span>
          <Input type="date" value={OVERVIEW_RANGE_END} readOnly className="w-40 bg-background/70 font-mono" />
        </label>
        <Button type="button" variant="outline" size="sm">{t('timeFilter.reset')}</Button>
      </div>
      <div className="text-sm font-medium tabular-nums text-muted-foreground">
        {t('timeFilter.current')}: {OVERVIEW_RANGE_START} ~ {OVERVIEW_RANGE_END}
      </div>
    </Panel>
  )
}

function TokenTrendCard({ t }: { t: (key: string) => string }) {
  return (
    <Panel>
      <h2 className="mb-5 text-lg font-semibold tracking-tight">{t('tokenTrend.title')}</h2>
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={TOKEN_TREND_DATA} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="tokenTrendInput" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="oklch(0.6 0.12 210)" stopOpacity={0.5} />
                <stop offset="95%" stopColor="oklch(0.6 0.12 210)" stopOpacity={0.12} />
              </linearGradient>
              <linearGradient id="tokenTrendOutput" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="oklch(0.5 0.134 243)" stopOpacity={0.45} />
                <stop offset="95%" stopColor="oklch(0.5 0.134 243)" stopOpacity={0.1} />
              </linearGradient>
              <linearGradient id="tokenTrendVector" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="oklch(0.6 0.1 280)" stopOpacity={0.38} />
                <stop offset="95%" stopColor="oklch(0.6 0.1 280)" stopOpacity={0.08} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="currentColor" strokeOpacity={0.08} vertical={false} />
            <XAxis
              dataKey="date"
              axisLine={false}
              tickLine={false}
              tick={{ fill: 'currentColor', fontSize: 12 }}
              className="text-muted-foreground"
            />
            <Tooltip
              cursor={{ stroke: 'currentColor', strokeOpacity: 0.12 }}
              contentStyle={{
                background: 'hsl(var(--popover))',
                border: '1px solid hsl(var(--border))',
                borderRadius: 12,
                color: 'hsl(var(--popover-foreground))',
              }}
              formatter={(value, name) => [
                Number(value).toLocaleString(),
                t(TOKEN_TREND_KEYS.find((item) => item.dataKey === name)?.labelKey ?? 'tokenTrend.input'),
              ]}
            />
            <Area type="monotone" dataKey="input" stackId="tokens" stroke="oklch(0.6 0.12 210)" strokeWidth={2} fill="url(#tokenTrendInput)" />
            <Area type="monotone" dataKey="output" stackId="tokens" stroke="oklch(0.5 0.134 243)" strokeWidth={2} fill="url(#tokenTrendOutput)" />
            <Area type="monotone" dataKey="vector" stackId="tokens" stroke="oklch(0.6 0.1 280)" strokeWidth={2} fill="url(#tokenTrendVector)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-4 flex flex-wrap gap-4">
        {TOKEN_TREND_KEYS.map((item) => (
          <div key={item.dataKey} className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="size-2.5 rounded-full" style={{ backgroundColor: item.color }} />
            <span>{t(item.labelKey)}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

const SESSION_STATUS_STYLES: Record<string, { bg: string; text: string; darkBg: string; darkText: string }> = {
  active:    { bg: 'oklch(0.6 0.12 180 / 0.15)', text: 'oklch(0.6 0.12 180)', darkBg: 'oklch(0.7 0.12 180 / 0.25)', darkText: 'oklch(0.7 0.12 180)' },
  committed: { bg: 'oklch(0.6 0.12 210 / 0.15)', text: 'oklch(0.6 0.12 210)', darkBg: 'oklch(0.7 0.13 210 / 0.25)', darkText: 'oklch(0.7 0.13 210)' },
  archived:  { bg: 'oklch(0.56 0.021 213.5 / 0.15)', text: 'oklch(0.56 0.021 213.5)', darkBg: 'oklch(0.56 0.021 213.5 / 0.25)', darkText: 'oklch(0.708 0 0)' },
  expired:   { bg: 'oklch(0.55 0.2 15 / 0.15)', text: 'oklch(0.55 0.2 15)', darkBg: 'oklch(0.65 0.2 15 / 0.25)', darkText: 'oklch(0.65 0.2 15)' },
}

function SessionsCard({
  data,
  isLoading,
  isError,
  t,
}: {
  data: unknown
  isLoading: boolean
  isError: boolean
  t: (key: string) => string
}) {
  const isDark = useIsDark()
  const sessions = asArray(data).slice(0, 10)

  return (
    <Panel>
      <h2 className="mb-1 text-lg font-semibold tracking-tight">{t('sessions.title')}</h2>
      <p className="mb-5 text-sm text-muted-foreground">{t('sessions.subtitle')}</p>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <span className="text-sm text-destructive">{t('requestFailed')}</span>
      ) : sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-8 text-muted-foreground">
          <Users className="size-8 opacity-40" />
          <p className="text-sm">{t('sessions.empty')}</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Session ID</TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Status</TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessions.map((s, i) => {
              const session = asRecord(s)
              return (
                <TableRow key={asString(session.session_id) || i} className="border-foreground/5 transition-colors hover:bg-muted/40">
                  <TableCell className="font-mono text-sm" title={asString(session.session_id)}>{truncate(asString(session.session_id), 8)}</TableCell>
                  <TableCell>
                    {(() => {
                      const status = asString(session.status) || 'active'
                      const style = SESSION_STATUS_STYLES[status] ?? SESSION_STATUS_STYLES.active!
                      return (
                        <span
                          className="inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium"
                          style={{
                            backgroundColor: isDark ? style!.darkBg : style!.bg,
                            color: isDark ? style!.darkText : style!.text,
                          }}
                        >
                          {status}
                        </span>
                      )
                    })()}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{asString(session.created_at).slice(0, 10)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}

// ---------- main ----------

export function HomePage() {
  const { t } = useTranslation('home')

  const systemStatus = useQuery({
    queryKey: ['system-status'],
    queryFn: () => getOvResult(getSystemStatus()),
  })

  const observerSystem = useQuery({
    queryKey: ['observer-system'],
    queryFn: () => getOvResult(getObserverSystem()),
  })

  const memoryStats = useQuery({
    queryKey: ['stats-memories'],
    queryFn: () => getOvResult(getStatsMemories()),
  })

  const vectorCount = useQuery({
    queryKey: ['debug-vector-count'],
    queryFn: () => getOvResult(getDebugVectorCount()),
  })

  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => getOvResult(getTasks()),
  })

  const sessions = useQuery({
    queryKey: ['sessions'],
    queryFn: () => getOvResult(getSessions()),
  })

  const tokenStats = useQuery({
    queryKey: ['stats-tokens'],
    queryFn: () => fetchTokenStats(),
  })

  const memRecord = asRecord(memoryStats.data)
  const vecRecord = asRecord(vectorCount.data)
  const tokenRecord = asRecord(tokenStats.data)

  const containerRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const children = el.children
    gsap.set(children, { opacity: 0, y: 30 })
    gsap.to(children, {
      opacity: 1,
      y: 0,
      duration: 0.5,
      stagger: 0.1,
      ease: 'power2.out',
    })
  }, [])

  return (
    <div ref={containerRef} className="flex flex-col gap-6 pb-8">
      {/* Row 1: Summary cards */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title={t('statCard.contextMagnitude')}
          subtitle={t('statCard.contextMagnitudeSub')}
          value={asNumber(vecRecord.count)}
          isLoading={vectorCount.isLoading}
          isError={vectorCount.isError}
          errorText={t('requestFailed')}
          accentColor="oklch(0.5 0.134 243)"
          icon={Database}
        />
        <StatCard
          title={t('statCard.tokenUsage')}
          subtitle={t('statCard.tokenUsageSub')}
          value={asNumber(tokenRecord.total_tokens)}
          isLoading={tokenStats.isLoading}
          isError={tokenStats.isError}
          errorText={t('requestFailed')}
          accentColor="oklch(0.5 0.134 243)"
          icon={Coins}
        />
        <StatCard
          title={t('statCard.retrievalCount')}
          subtitle={t('statCard.retrievalCountSub')}
          value={asNumber(memRecord.total_memories)}
          isLoading={memoryStats.isLoading}
          isError={memoryStats.isError}
          errorText={t('requestFailed')}
          accentColor="oklch(0.5 0.134 243)"
          icon={Brain}
        />
        <StatCard
          title={t('statCard.agentVisits')}
          subtitle={t('statCard.agentVisitsSub')}
          value={asArray(sessions.data).length}
          isLoading={sessions.isLoading}
          isError={sessions.isError}
          errorText={t('requestFailed')}
          accentColor="oklch(0.5 0.134 243)"
          icon={Users}
        />
      </div>

      {/* Row 2: Time filter */}
      <TimeRangeFilter t={t} />

      {/* Row 3: Token trend */}
      <TokenTrendCard t={t} />

      {/* Row 4: Contribution heatmap demo */}
      <ContributionHeatmapDemo t={t} />

      {/* Row 5: System health */}
      <ComponentHealthBar
        data={observerSystem.data}
        sysData={systemStatus.data}
        isLoading={observerSystem.isLoading}
        sysLoading={systemStatus.isLoading}
        isError={observerSystem.isError}
        error={observerSystem.error}
        t={t}
      />

      {/* Row 6: Memory stats + Tasks */}
      <div className="grid gap-4 md:grid-cols-2">
        <MemoryStatsCard
          data={memoryStats.data}
          isLoading={memoryStats.isLoading}
          isError={memoryStats.isError}
          t={t}
        />
        <RecentTasksCard
          data={tasks.data}
          isLoading={tasks.isLoading}
          isError={tasks.isError}
          t={t}
        />
      </div>

      {/* Row 7: Sessions */}
      <SessionsCard
        data={sessions.data}
        isLoading={sessions.isLoading}
        isError={sessions.isError}
        t={t}
      />
    </div>
  )
}
