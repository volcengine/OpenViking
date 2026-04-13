import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Cell, Label, Pie, PieChart } from 'recharts'
import gsap from 'gsap'
import { AlertCircle, Brain, Coins, ChevronDown, Copy, Check, Database, ListTodo, Users } from 'lucide-react'

import { Button } from '#/components/ui/button'
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

// ---------- category colors (monochrome shades) ----------

const CATEGORY_COLORS: Record<string, string> = {
  profile: '#18181b',
  preferences: '#3f3f46',
  entities: '#52525b',
  events: '#71717a',
  cases: '#a1a1aa',
  patterns: '#d4d4d8',
  tools: '#e4e4e7',
  skills: '#f4f4f5',
}

const CATEGORY_COLORS_DARK: Record<string, string> = {
  profile: '#fafafa',
  preferences: '#d4d4d8',
  entities: '#a1a1aa',
  events: '#71717a',
  cases: '#52525b',
  patterns: '#3f3f46',
  tools: '#27272a',
  skills: '#18181b',
}

const CATEGORY_ORDER = ['profile', 'preferences', 'entities', 'events', 'cases', 'patterns', 'tools', 'skills']

// ---------- status helpers ----------

const STATUS_COLORS: Record<string, string> = {
  completed: '#7e9e7e', // sage green
  running: '#c4a882',   // warm tan
  failed: '#b07e7e',    // dusty rose
  pending: '#b0aaa2',   // warm gray
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
    <div className={`rounded-2xl bg-muted/50 p-6 transition-colors duration-200 hover:bg-muted/70 dark:bg-white/[0.08] dark:hover:bg-white/[0.12] ${className}`}>
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
  icon: React.ComponentType<{ className?: string }>
}) {
  const valueRef = useRef<HTMLSpanElement>(null)
  const hasAnimated = useRef(false)

  useEffect(() => {
    if (isLoading || isError || hasAnimated.current) return
    const el = valueRef.current
    if (!el) return
    const target = typeof value === 'number' ? value : Number(String(value).replace(/,/g, ''))
    if (Number.isNaN(target) || target === 0) return
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
      className="relative flex flex-col justify-between gap-4 overflow-hidden rounded-2xl bg-muted/50 p-6 transition-all duration-200 hover:-translate-y-0.5 hover:bg-muted/70 hover:shadow-md dark:bg-white/[0.08] dark:hover:bg-white/[0.12]"
      style={{ borderLeft: `2px solid ${accentColor}` }}
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
  t: (key: string) => string
}) {
  const [selectedComponent, setSelectedComponent] = useState<{
    name: string
    payload: Record<string, unknown>
  } | null>(null)
  const [showDialogScrollbar, setShowDialogScrollbar] = useState(false)
  const dialogScrollRef = useRef<HTMLDivElement | null>(null)
  const hideScrollbarTimerRef = useRef<number | null>(null)
  const record = asRecord(data)
  const sys = asRecord(sysData)
  const components = { ...asRecord(record.components) }
  const names = ['queue', 'vikingdb', 'models', 'lock', 'retrieval']
  const systemHealthy = sys.initialized === true

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
          <div className="flex items-center gap-2">
              {overallHealthy && displaySystemHealthy
                ? <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: '#7e9e7e' }} />
                : <BreathingDot color="#b07e7e" />
              }
              <span className="text-sm text-muted-foreground">{overallHealthy && displaySystemHealthy ? t('systemHealth.allOperational') : t('systemHealth.issuesDetected')}</span>
            </div>
          )}
        </div>
        {isLoading || sysLoading ? (
          <div className="flex gap-4">
            {names.map((n) => <Skeleton key={n} className="h-10 w-32" />)}
          </div>
        ) : isError ? (
          <div className="space-y-1">
            <span className="text-sm text-destructive">{t('requestFailed')}</span>
            {error?.message && (
              <pre className="max-h-24 overflow-auto rounded-lg bg-foreground/[0.03] p-3 text-xs text-muted-foreground">{error.message}</pre>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <div className="flex items-center gap-2.5 rounded-xl bg-foreground/[0.03] px-4 py-3 dark:bg-foreground/[0.06]">
              {displaySystemHealthy
                ? <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: '#7e9e7e' }} />
                : <BreathingDot color="#b07e7e" />
              }
              <span className="text-sm font-medium">System</span>
            </div>
            {names.map((name) => {
              const comp = asRecord(components[name])
              const healthy = comp.is_healthy === true
              const hasIssues = comp.has_errors === true || !healthy
              return (
                <div
                  key={name}
                  className="flex items-center justify-between gap-2.5 rounded-xl bg-foreground/[0.03] px-4 py-3 dark:bg-foreground/[0.06]"
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    {healthy
                      ? <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: '#7e9e7e' }} />
                      : <BreathingDot color="#b07e7e" />
                    }
                    <span className="truncate text-sm font-medium capitalize">{name}</span>
                  </div>
                  {hasIssues && (
                    <button
                      type="button"
                      aria-label={`${t('systemHealth.viewErrorAria')} ${name}`}
                      className="inline-flex size-6 shrink-0 items-center justify-center rounded-full text-[#b07e7e] transition hover:text-[#9d6767]"
                      onClick={() => openComponentDetails(name, comp)}
                    >
                      <AlertCircle className="size-3.5" />
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Panel>

      <Dialog open={selectedComponent !== null} onOpenChange={(open) => !open && setSelectedComponent(null)}>
        <DialogContent className="max-h-[min(88vh,720px)] max-w-lg gap-0 overflow-hidden p-0">
          <DialogHeader>
            <div className="border-b border-border/60 px-6 pt-6 pb-4">
              <div className="pt-3">
                <DialogTitle>
                {selectedComponent ? `${selectedComponent.name} ${t('systemHealth.dialogTitle')}` : t('systemHealth.dialogTitle')}
                </DialogTitle>
                <DialogDescription className="mt-1">
                {t('systemHealth.dialogDescription')}
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>
          <div
            ref={dialogScrollRef}
            className={[
              'overflow-y-auto px-6 py-5',
              'max-h-[calc(min(88vh,720px)-172px)]',
              '[scrollbar-width:none]',
              '[&::-webkit-scrollbar]:w-0',
              showDialogScrollbar
                ? '[scrollbar-width:thin] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border/80 [&::-webkit-scrollbar-track]:bg-transparent'
                : '',
            ].join(' ')}
          >
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-muted/60 p-4 text-sm dark:bg-white/[0.06]">
                <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">name</div>
                <div className="font-medium">{asString(selectedComponent?.payload.name)}</div>
              </div>
              <div className="rounded-xl bg-muted/60 p-4 text-sm dark:bg-white/[0.06]">
                <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">is_healthy</div>
                <div className="font-medium">{String(selectedComponent?.payload.is_healthy === true)}</div>
              </div>
              <div className="rounded-xl bg-muted/60 p-4 text-sm dark:bg-white/[0.06]">
                <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">has_errors</div>
                <div className="font-medium">{String(selectedComponent?.payload.has_errors === true)}</div>
              </div>
              <div className="rounded-xl bg-muted/60 p-4 text-sm dark:bg-white/[0.06]">
                <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">errors.length</div>
                <div className="font-medium">{asStringArray(selectedComponent?.payload.errors).length}</div>
              </div>
            </div>
            <div className="rounded-xl bg-muted/60 p-4 text-sm leading-6 text-muted-foreground dark:bg-white/[0.06]">
              <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">status</div>
              <div className="whitespace-pre-wrap break-words">
                {asString(selectedComponent?.payload.status) || t('systemHealth.noDetails')}
              </div>
            </div>
            <div className="rounded-xl bg-muted/60 p-4 text-sm leading-6 text-muted-foreground dark:bg-white/[0.06]">
              <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">errors</div>
              {asStringArray(selectedComponent?.payload.errors).length > 0 ? (
                <div className="space-y-2">
                  {asStringArray(selectedComponent?.payload.errors).map((item, index) => (
                    <div key={`${selectedComponent?.name}-error-${index}`} className="whitespace-pre-wrap break-words">
                      {item}
                    </div>
                  ))}
                </div>
              ) : (
                <div>{t('systemHealth.noDetails')}</div>
              )}
            </div>
            <div className="rounded-xl bg-muted/60 p-4 text-sm leading-6 text-muted-foreground dark:bg-white/[0.06]">
              <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">raw json</div>
              <pre className="overflow-auto whitespace-pre-wrap break-words text-xs">
                {JSON.stringify(selectedComponent?.payload ?? {}, null, 2)}
              </pre>
            </div>
          </div>
          </div>
          <DialogFooter className="border-t border-border/60 px-6 py-5">
            <Button variant="outline" onClick={() => setSelectedComponent(null)}>
              {t('systemHealth.close')}
            </Button>
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
                stroke={isDark ? 'hsl(240 3.7% 15.9%)' : 'hsl(0 0% 100%)'}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.name} fill={colors[entry.name] ?? '#94a3b8'} />
                ))}
                <Label
                  value={String(total)}
                  position="center"
                  fill={isDark ? '#fafafa' : '#18181b'}
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
                    className="inline-block size-3 shrink-0 rounded-full"
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

  return (
    <Panel>
      <h2 className="mb-1 text-lg font-semibold tracking-tight">{t('recentTasks.title')}</h2>
      <p className="mb-5 text-sm text-muted-foreground">{t('recentTasks.subtitle')}</p>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <span className="text-sm text-destructive">{t('requestFailed')}</span>
      ) : tasks.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('recentTasks.empty')}</p>
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
            {tasks.map((t, i) => {
              const task = asRecord(t)
              return (
                <TableRow key={asString(task.task_id) || i} className="border-foreground/5 hover:bg-foreground/[0.02]">
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

const SESSION_STATUS_STYLES: Record<string, { bg: string; text: string; darkBg: string; darkText: string }> = {
  active:    { bg: 'rgba(126,158,126,0.15)', text: '#7e9e7e', darkBg: 'rgba(126,158,126,0.25)', darkText: '#a4c4a4' },
  committed: { bg: 'rgba(142,154,175,0.15)', text: '#8e9aaf', darkBg: 'rgba(142,154,175,0.25)', darkText: '#b0bcd0' },
  archived:  { bg: 'rgba(176,170,162,0.15)', text: '#8d8478', darkBg: 'rgba(176,170,162,0.25)', darkText: '#b8aea2' },
  expired:   { bg: 'rgba(176,126,126,0.15)', text: '#b07e7e', darkBg: 'rgba(176,126,126,0.25)', darkText: '#d0a0a0' },
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
        <p className="text-sm text-muted-foreground">{t('sessions.empty')}</p>
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
                <TableRow key={asString(session.session_id) || i} className="border-foreground/5 hover:bg-foreground/[0.02]">
                  <TableCell className="font-mono text-sm">{truncate(asString(session.session_id), 24)}</TableCell>
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
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          title={t('statCard.vectorCount')}
          subtitle={t('statCard.vectorCountSub')}
          value={asNumber(vecRecord.count)}
          isLoading={vectorCount.isLoading}
          isError={vectorCount.isError}
          errorText={t('requestFailed')}
          accentColor="#6b8cce"
          icon={Database}
        />
        <StatCard
          title={t('statCard.memoryTotal')}
          subtitle={t('statCard.memoryTotalSub')}
          value={asNumber(memRecord.total_memories)}
          isLoading={memoryStats.isLoading}
          isError={memoryStats.isError}
          errorText={t('requestFailed')}
          accentColor="#7e9e7e"
          icon={Brain}
        />
        <StatCard
          title={t('statCard.tokenUsage')}
          subtitle={t('statCard.tokenUsageSub')}
          value={asNumber(tokenRecord.total_tokens)}
          isLoading={tokenStats.isLoading}
          isError={tokenStats.isError}
          errorText={t('requestFailed')}
          accentColor="#c4a882"
          icon={Coins}
        />
      </div>

      {/* Row 2: System health */}
      <ComponentHealthBar
        data={observerSystem.data}
        sysData={systemStatus.data}
        isLoading={observerSystem.isLoading}
        sysLoading={systemStatus.isLoading}
        isError={observerSystem.isError}
        error={observerSystem.error}
        t={t}
      />

      {/* Row 3: Memory stats + Tasks */}
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

      {/* Row 4: Sessions */}
      <SessionsCard
        data={sessions.data}
        isLoading={sessions.isLoading}
        isError={sessions.isError}
        t={t}
      />
    </div>
  )
}
