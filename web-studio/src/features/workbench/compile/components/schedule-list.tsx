import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ContextLink } from '#/features/workbench/compile/components/context-link'
import {
  CompileError,
  CompileLoading,
} from '#/features/workbench/compile/components/shared'
import { fetchCompileConfigs } from '#/features/workbench/compile/lib/api'
import { Badge } from '#/shared/ui/badge'
import { Button } from '#/shared/ui/button'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CalendarClockIcon, RefreshCwIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

const SCHEDULE_TIME_ZONE = 'Asia/Shanghai'

export function CompileScheduleList() {
  const { t, i18n } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
  const navigate = useNavigate()
  const [filter, setFilter] = useState('all')
  const query = useInfiniteQuery({
    queryKey: ['compile-configs', identityScopeKey],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => fetchCompileConfigs(pageParam, signal),
    getNextPageParam: (page) => page.next_cursor || undefined,
  })
  const targets = [
    ...new Map(
      query.data?.pages.flatMap((page) =>
        page.targets.map((target) => [target.to, target] as const),
      ),
    ).values(),
  ].filter(
    (target) =>
      filter === 'all' || target.auto_update === (filter === 'active'),
  )

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        {t('schedule.listDescription')}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label={t('schedule.filter')}
          className="h-9 rounded-md border bg-background px-3 text-sm"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">{t('schedule.allTargets')}</option>
          <option value="active">{t('schedule.active')}</option>
          <option value="disabled">{t('schedule.disabled')}</option>
        </select>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon />
          {t('refresh')}
        </Button>
      </div>
      {query.isLoading && <CompileLoading />}
      {query.isError && (
        <CompileError error={query.error} retry={() => void query.refetch()} />
      )}
      {!query.isLoading && !query.isError && !targets.length && (
        <div className="grid justify-items-center gap-3 rounded-xl border border-dashed px-4 py-16 text-center">
          <CalendarClockIcon className="size-8 text-muted-foreground" />
          <h2 className="font-medium">
            {t(
              query.hasNextPage
                ? 'schedule.emptyLoaded'
                : filter === 'all'
                  ? 'schedule.empty'
                  : 'schedule.emptyFiltered',
            )}
          </h2>
        </div>
      )}
      {!!targets.length && (
        <ul className="overflow-hidden rounded-xl border">
          {targets.map((target) => {
            const schedule = target.update_schedule
            const time = schedule
              ? `${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}`
              : ''
            const nextRun = target.next_run_at
              ? new Date(target.next_run_at)
              : null
            const validNextRun = nextRun && !Number.isNaN(nextRun.getTime())
            return (
              <li
                key={target.to}
                className="space-y-4 border-b bg-card/40 p-5 last:border-b-0"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <h2 className="break-words text-sm font-medium [overflow-wrap:anywhere]">
                      <ContextLink uri={target.to}>{target.to}</ContextLink>
                    </h2>
                    <Badge
                      variant={target.auto_update ? 'default' : 'secondary'}
                    >
                      {t(
                        target.auto_update
                          ? 'schedule.active'
                          : 'schedule.disabled',
                      )}
                    </Badge>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`${t('schedule.edit')}: ${target.to}`}
                    onClick={() =>
                      void navigate({
                        to: '/compile/new',
                        search: { target: target.to },
                      })
                    }
                  >
                    {t('schedule.edit')}
                  </Button>
                </div>
                <dl className="grid gap-4 text-sm sm:grid-cols-3">
                  <div className="space-y-1">
                    <dt className="text-xs text-muted-foreground">
                      {t('schedule.timing')}
                    </dt>
                    <dd>
                      {schedule
                        ? schedule.frequency === 'daily'
                          ? t('schedule.dailyAt', { time })
                          : t('schedule.weeklyAt', {
                              time,
                              weekday: t(
                                `schedule.weekdays.${schedule.weekday}`,
                              ),
                            })
                        : t('schedule.noSchedule')}
                    </dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-xs text-muted-foreground">
                      {t('schedule.nextRun')}
                    </dt>
                    <dd>
                      {validNextRun ? (
                        <time dateTime={nextRun.toISOString()}>
                          {nextRun.toLocaleString(i18n.language, {
                            timeZone: SCHEDULE_TIME_ZONE,
                          })}
                        </time>
                      ) : (
                        t('schedule.notScheduled')
                      )}
                    </dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-xs text-muted-foreground">
                      {t('schedule.execution')}
                    </dt>
                    <dd>
                      {t(target.running ? 'schedule.running' : 'schedule.idle')}
                    </dd>
                  </div>
                </dl>
              </li>
            )
          })}
        </ul>
      )}
      {query.hasNextPage && (
        <Button
          variant="outline"
          className="self-center"
          disabled={query.isFetching}
          onClick={() => void query.fetchNextPage()}
        >
          {t(query.isFetchingNextPage ? 'loading' : 'more')}
        </Button>
      )}
    </div>
  )
}
