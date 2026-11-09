import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileSelect } from '#/features/workbench/compile/components/compile-select'
import { CompileScheduleTarget } from '#/features/workbench/compile/components/schedule-target'
import {
  CompileError,
  CompileLoading,
} from '#/features/workbench/compile/components/shared'
import { fetchCompileConfigs } from '#/features/workbench/compile/lib/api'
import { Button } from '#/shared/ui/button'
import { useInfiniteQuery } from '@tanstack/react-query'
import { CalendarClockIcon, RefreshCwIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function CompileScheduleList() {
  const { t } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
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
        <CompileSelect
          label={t('schedule.filter')}
          className="min-w-36"
          value={filter}
          onValueChange={setFilter}
          options={[
            { value: 'all', label: t('schedule.allTargets') },
            { value: 'active', label: t('schedule.active') },
            { value: 'disabled', label: t('schedule.disabled') },
          ]}
        />
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon
            className={query.isFetching ? 'animate-spin' : undefined}
          />
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
          {targets.map((target) => (
            <CompileScheduleTarget
              key={`${identityScopeKey}:${target.to}`}
              target={target}
            />
          ))}
        </ul>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t('schedule.disableHint')}
      </p>
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
