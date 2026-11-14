import { AdminScopeFilters } from '#/features/admin/users/components/admin-scope-filters'
import { useAdminSpaceHeaders } from '#/features/connection/hooks/use-admin-space-headers'
import styles from './page.module.css'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
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
import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '#/shared/ui/alert'
import { Badge } from '#/shared/ui/badge'
import { Button } from '#/shared/ui/button'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ObserverStatusContent } from '#/features/workbench/monitoring/components/observer-status-content'
import { stripVersionPrefix } from '#/features/workbench/monitoring/lib/version'
import {
  getHealth,
  getObserverSystem,
  getOvResult,
} from '#/shared/api/ov-client/index'
import { cn } from '#/shared/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '#/shared/ui/card'

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

async function fetchMonitoringOverview(
  headers?: Record<string, string>,
): Promise<MonitoringOverview> {
  const [health, observer] = await Promise.all([
    getOvResult<Record<string, unknown>>(getHealth()),
    getOvResult<Record<string, unknown>>(getObserverSystem({ headers })),
  ])
  const rawComponents = isRecord(observer.components) ? observer.components : {}
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
  healthy: boolean | undefined
  label: string
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        styles.badge,
        healthy === undefined
          ? 'text-muted-foreground'
          : healthy
            ? 'border-emerald-500/30 text-emerald-600'
            : 'border-destructive/30 text-destructive',
      )}
    >
      <span
        className={cn(
          'size-1.5 rounded-full',
          healthy === undefined
            ? 'bg-muted-foreground'
            : healthy
              ? 'bg-emerald-500'
              : 'bg-destructive',
        )}
      />
      {label}
    </Badge>
  )
}

export function MonitoringRoute() {
  const { i18n, t } = useTranslation('monitoringPage')
  const { identityScopeKey, serverMode } = useAppConnection()
  const scoped = useAdminSpaceHeaders(true)
  const [activeType, setActiveType] = React.useState<MonitorType>('overview')
  const monitoringQuery = useQuery({
    enabled:
      serverMode !== 'offline' && !scoped.isLoading && scoped.headers !== null,
    queryFn: () => fetchMonitoringOverview(scoped.headers ?? undefined),
    queryKey: ['monitoring-overview', identityScopeKey, scoped.scopeKey],
    meta: { adminSpaceScope: scoped.scopeKey },
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
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>{t('title')}</h1>
        <div className={styles.headerControls}>
          {overview?.version && (
            <span>
              {t('version', { version: stripVersionPrefix(overview.version) })}
            </span>
          )}
          {updatedAt && <span>{t('updatedAt', { time: updatedAt })}</span>}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t('refresh')}
            title={t('refresh')}
            disabled={monitoringQuery.isFetching}
            onClick={() => void monitoringQuery.refetch()}
          >
            <RefreshCwIcon
              className={cn(
                'size-4',
                monitoringQuery.isFetching && 'animate-spin',
              )}
            />
          </Button>
        </div>
      </header>
      {(activeType === 'overview' ||
        activeType === 'models' ||
        activeType === 'vikingdb') && <AdminScopeFilters />}
      {!scoped.isLoading && scoped.headers === null && (
        <p role="status">{t(scoped.unavailableKey)}</p>
      )}

      {serverMode === 'offline' ? (
        <Alert>
          <CircleAlertIcon />
          <AlertTitle>{t('offline.title')}</AlertTitle>
          <AlertDescription>
            {t('offline.description')}{' '}
            <Link
              to="/admin/settings"
              className="font-medium text-primary underline"
            >
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
          <Card className={styles.summary}>
            <CardContent className={styles.summaryContent}>
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    styles.summaryIcon,
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
            className={styles.tabs}
          >
            {MONITOR_TYPES.map(([name, Icon]) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={activeType === name}
                id={`monitor-tab-${name}`}
                aria-controls="monitor-panel"
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
            <div
              className={styles.grid}
              role="tabpanel"
              id="monitor-panel"
              aria-labelledby="monitor-tab-overview"
            >
              {MONITOR_TYPES.slice(1).map(([name, Icon]) => {
                const component = overview?.components[name]
                const healthy = component
                  ? component.is_healthy && !component.has_errors
                  : undefined
                return (
                  <button
                    key={name}
                    type="button"
                    className="group rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => setActiveType(name)}
                  >
                    <Card className={styles.componentCard}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <div className={styles.componentIcon}>
                            <Icon className="size-4" />
                          </div>
                          <CardTitle>{t(`tabs.${name}`)}</CardTitle>
                        </div>
                        <HealthBadge
                          healthy={healthy}
                          label={
                            healthy === undefined
                              ? t('health.unknown')
                              : healthy
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
            <Card
              role="tabpanel"
              id="monitor-panel"
              aria-labelledby={`monitor-tab-${activeType}`}
              className="gap-0 overflow-hidden py-0"
            >
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
                      selectedComponent
                        ? selectedComponent.is_healthy &&
                          !selectedComponent.has_errors
                        : undefined
                    }
                    label={
                      !selectedComponent
                        ? t('health.unknown')
                        : selectedComponent.is_healthy &&
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
