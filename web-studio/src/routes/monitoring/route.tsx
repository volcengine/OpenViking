import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import {
  ActivityIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  CpuIcon,
  DatabaseIcon,
  HardDriveIcon,
  LayoutDashboardIcon,
  ListTodoIcon,
  LockKeyholeIcon,
  RefreshCwIcon,
  SearchIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { useAppConnection } from '#/hooks/use-app-connection'
import { getHealth, getObserverSystem, getOvResult } from '#/lib/ov-client'
import { cn } from '#/lib/utils'
import { parseObserverStatus } from './-lib/parse-status'

export const Route = createFileRoute('/monitoring')({
  component: MonitoringRoute,
})

type ObserverComponent = {
  has_errors: boolean
  is_healthy: boolean
  name: string
  status: string
}

type MonitoringOverview = {
  components: Record<string, ObserverComponent>
  errors: string[]
  healthy: boolean
  version?: string
}

const MONITOR_TYPES = [
  ['overview', LayoutDashboardIcon],
  ['queue', ListTodoIcon],
  ['vikingdb', DatabaseIcon],
  ['models', CpuIcon],
  ['filesystem', HardDriveIcon],
  ['lock', LockKeyholeIcon],
  ['retrieval', SearchIcon],
] as const

type MonitorType = (typeof MONITOR_TYPES)[number][0]

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeComponent(
  name: string,
  value: unknown,
): ObserverComponent | undefined {
  if (!isRecord(value)) return undefined

  return {
    has_errors: value.has_errors === true,
    is_healthy: value.is_healthy === true,
    name: typeof value.name === 'string' ? value.name : name,
    status: typeof value.status === 'string' ? value.status : '',
  }
}

async function fetchMonitoringOverview(): Promise<MonitoringOverview> {
  const [health, observer] = await Promise.all([
    getOvResult<Record<string, unknown>>(getHealth()),
    getOvResult<Record<string, unknown>>(getObserverSystem()),
  ])
  const rawComponents = isRecord(observer.components)
    ? observer.components
    : {}
  const components: Record<string, ObserverComponent> = {}

  for (const [name] of MONITOR_TYPES) {
    if (name === 'overview') continue
    const component = normalizeComponent(name, rawComponents[name])
    if (component) components[name] = component
  }

  return {
    components,
    errors: Array.isArray(observer.errors)
      ? observer.errors.filter(
          (error): error is string => typeof error === 'string',
        )
      : [],
    healthy: observer.is_healthy === true,
    version: typeof health.version === 'string' ? health.version : undefined,
  }
}

function HealthBadge({
  healthy,
  label,
}: {
  healthy: boolean
  label: string
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'gap-1.5 font-normal',
        healthy
          ? 'border-emerald-500/30 text-emerald-600'
          : 'border-destructive/30 text-destructive',
      )}
    >
      <span
        className={cn(
          'size-1.5 rounded-full',
          healthy ? 'bg-emerald-500' : 'bg-destructive',
        )}
      />
      {label}
    </Badge>
  )
}

function ObserverStatusContent({ status }: { status: string }) {
  const { t } = useTranslation('monitoringPage')
  const blocks = React.useMemo(() => parseObserverStatus(status), [status])

  if (blocks.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">{t('detail.noData')}</p>
    )
  }

  return (
    <div className="grid gap-4">
      {blocks.map((block, blockIndex) =>
        block.kind === 'text' ? (
          <p
            key={`${block.value}-${blockIndex}`}
            className="rounded-lg border bg-muted/20 px-3 py-2 text-sm text-muted-foreground"
          >
            {block.value}
          </p>
        ) : (
          <div
            key={`table-${blockIndex}`}
            className="overflow-x-auto rounded-lg border"
          >
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  {block.headers.map((header, headerIndex) => (
                    <TableHead
                      key={`${header}-${headerIndex}`}
                      className="whitespace-nowrap"
                    >
                      {header}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {block.rows.map((row, rowIndex) => (
                  <TableRow key={`row-${rowIndex}`}>
                    {row.map((cell, cellIndex) => (
                      <TableCell
                        key={`${cell}-${cellIndex}`}
                        className="whitespace-nowrap font-mono text-xs"
                      >
                        {cell}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ),
      )}
    </div>
  )
}

function MonitoringRoute() {
  const { i18n, t } = useTranslation('monitoringPage')
  const { identityScopeKey, serverMode } = useAppConnection()
  const [activeType, setActiveType] = React.useState<MonitorType>('overview')
  const monitoringQuery = useQuery({
    enabled: serverMode !== 'offline',
    queryFn: fetchMonitoringOverview,
    queryKey: ['monitoring-overview', identityScopeKey],
    refetchInterval: 10_000,
    retry: false,
    staleTime: 5_000,
  })
  const overview = monitoringQuery.data
  const selectedComponent =
    activeType === 'overview' ? undefined : overview?.components[activeType]
  const SelectedMonitorIcon =
    MONITOR_TYPES.find(([name]) => name === activeType)?.[1] ?? ActivityIcon
  const healthyCount = Object.values(overview?.components ?? {}).filter(
    (component) => component.is_healthy && !component.has_errors,
  ).length
  const totalCount = Object.keys(overview?.components ?? {}).length
  const updatedAt = monitoringQuery.dataUpdatedAt
    ? new Intl.DateTimeFormat(i18n.resolvedLanguage, {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(monitoringQuery.dataUpdatedAt)
    : undefined

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">
              {t('title')}
            </h1>
            {overview?.version ? (
              <Badge variant="outline" className="font-mono font-normal">
                {t('version', { version: overview.version })}
              </Badge>
            ) : null}
          </div>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('description')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {updatedAt ? (
            <span className="text-xs text-muted-foreground">
              {t('updatedAt', { time: updatedAt })}
            </span>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={monitoringQuery.isFetching}
            onClick={() => void monitoringQuery.refetch()}
          >
            <RefreshCwIcon
              className={cn(
                'size-4',
                monitoringQuery.isFetching && 'animate-spin',
              )}
            />
            {t('refresh')}
          </Button>
        </div>
      </header>

      {serverMode === 'offline' ? (
        <Alert>
          <CircleAlertIcon />
          <AlertTitle>{t('offline.title')}</AlertTitle>
          <AlertDescription>
            {t('offline.description')}{' '}
            <Link to="/settings" className="font-medium text-primary underline">
              {t('offline.action')}
            </Link>
          </AlertDescription>
        </Alert>
      ) : monitoringQuery.isLoading ? (
        <Card className="min-h-64 items-center justify-center">
          <RefreshCwIcon className="size-5 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        </Card>
      ) : monitoringQuery.isError ? (
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>{t('loadFailed')}</AlertTitle>
          <AlertDescription>
            {monitoringQuery.error instanceof Error
              ? monitoringQuery.error.message
              : String(monitoringQuery.error)}
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <Card className="gap-0 overflow-hidden py-0">
            <CardContent className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    'flex size-10 items-center justify-center rounded-xl',
                    overview?.healthy
                      ? 'bg-emerald-500/10 text-emerald-600'
                      : 'bg-destructive/10 text-destructive',
                  )}
                >
                  {overview?.healthy ? (
                    <CheckCircle2Icon className="size-5" />
                  ) : (
                    <CircleAlertIcon className="size-5" />
                  )}
                </div>
                <div>
                  <p className="font-medium">
                    {overview?.healthy
                      ? t('summary.healthy')
                      : t('summary.unhealthy')}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {t('summary.components', {
                      healthy: healthyCount,
                      total: totalCount,
                    })}
                  </p>
                </div>
              </div>
              <HealthBadge
                healthy={overview?.healthy === true}
                label={
                  overview?.healthy
                    ? t('health.healthy')
                    : t('health.unhealthy')
                }
              />
            </CardContent>
          </Card>

          <div
            role="tablist"
            aria-label={t('tabs.label')}
            className="flex max-w-full gap-1 overflow-x-auto rounded-xl border bg-muted/20 p-1"
          >
            {MONITOR_TYPES.map(([name, Icon]) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={activeType === name}
                className={cn(
                  'flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  activeType === name
                    ? 'bg-background font-medium text-foreground shadow-xs'
                    : 'hover:bg-background/60 hover:text-foreground',
                )}
                onClick={() => setActiveType(name)}
              >
                <Icon className="size-4" />
                {t(`tabs.${name}`)}
              </button>
            ))}
          </div>

          {activeType === 'overview' ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {MONITOR_TYPES.slice(1).map(([name, Icon]) => {
                const component = overview?.components[name]
                const healthy =
                  component?.is_healthy === true && !component.has_errors
                return (
                  <button
                    key={name}
                    type="button"
                    className="group rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => setActiveType(name)}
                  >
                    <Card className="h-full gap-3 p-4 transition-colors group-hover:border-primary/35 group-hover:bg-muted/15">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <div className="flex size-8 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                            <Icon className="size-4" />
                          </div>
                          <CardTitle>{t(`tabs.${name}`)}</CardTitle>
                        </div>
                        <HealthBadge
                          healthy={healthy}
                          label={
                            healthy
                              ? t('health.healthy')
                              : t('health.unhealthy')
                          }
                        />
                      </div>
                      <p className="text-sm leading-6 text-muted-foreground">
                        {t(`detail.descriptions.${name}`)}
                      </p>
                    </Card>
                  </button>
                )
              })}
            </div>
          ) : (
            <Card className="gap-0 overflow-hidden py-0">
              <CardHeader className="border-b px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <SelectedMonitorIcon className="size-5 text-muted-foreground" />
                    <div>
                      <CardTitle>{t(`tabs.${activeType}`)}</CardTitle>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {t(`detail.descriptions.${activeType}`)}
                      </p>
                    </div>
                  </div>
                  <HealthBadge
                    healthy={
                      selectedComponent?.is_healthy === true &&
                      !selectedComponent.has_errors
                    }
                    label={
                      selectedComponent?.is_healthy &&
                      !selectedComponent.has_errors
                        ? t('health.healthy')
                        : t('health.unhealthy')
                    }
                  />
                </div>
              </CardHeader>
              <CardContent className="px-5 py-5">
                <ObserverStatusContent
                  status={selectedComponent?.status ?? ''}
                />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
