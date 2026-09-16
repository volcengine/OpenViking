import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  createFileRoute,
  Link,
  useNavigate,
  useLocation,
} from '@tanstack/react-router'
import {
  PlusIcon,
  RefreshCwIcon,
  SparklesIcon,
  FolderOutput,
  FolderInput,
  ChevronRight,
  Search,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  CompileError,
  CompileLoading,
  CompileShell,
  CompileStatus,
} from './-components/shared'
import { fetchCapabilities, fetchCompileTasks } from './-lib/api'

const STATUSES = [
  'pending',
  'running',
  'cancelling',
  'completed',
  'failed',
  'cancelled',
] as const

export const Route = createFileRoute('/compile/')({
  validateSearch: (
    search: Record<string, unknown>,
  ): { status?: string; q?: string } => ({
    status: typeof search.status === 'string' ? search.status : '',
    q: typeof search.q === 'string' ? search.q : '',
  }),
  component: CompileList,
})
function CompileList() {
  const { t, i18n } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
  const location = useLocation()
  const search = Route.useSearch(),
    navigate = useNavigate()
  const [input, setInput] = useState(search.q || '')
  useEffect(() => {
    setInput(search.q || '')
  }, [search.q])
  useEffect(() => {
    const timer = setTimeout(() => {
      if (input !== search.q)
        void navigate({
          to: '/compile',
          search: { ...search, q: input },
          replace: true,
        })
    }, 300)
    return () => clearTimeout(timer)
  }, [input, navigate, search])
  const query = useInfiniteQuery({
    queryKey: ['compile-list', identityScopeKey, search],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      fetchCompileTasks(search.status, search.q, pageParam, signal),
    getNextPageParam: (page) => page.next_cursor || undefined,
  })
  const capability = useQuery({
    queryKey: ['compile-capability', identityScopeKey],
    queryFn: fetchCapabilities,
    retry: false,
  })
  const latest = useQuery({
    queryKey: ['compile-latest', identityScopeKey, search],
    queryFn: ({ signal }) =>
      fetchCompileTasks(search.status, search.q, undefined, signal),
    enabled: !!query.data,
    refetchInterval: 10_000,
  })
  const latestById = new Map(
    latest.data?.items.map((task) => [task.task_id, task]),
  )
  const loadedIds = new Set(
    query.data?.pages.flatMap((page) => page.items.map((task) => task.task_id)),
  )
  const hasNew = latest.data?.items.some((task) => !loadedIds.has(task.task_id))
  const tasks = [
    ...new Map(
      query.data?.pages
        .flatMap((p) => p.items)
        .map((task) => [task.task_id, latestById.get(task.task_id) || task]),
    ).values(),
  ]
  return (
    <CompileShell
      back={false}
      title={t('title')}
      actions={
        <Button
          disabled={capability.data?.can_create === false}
          onClick={() => void navigate({ to: '/compile/new' })}
        >
          <PlusIcon />
          {t('new')}
        </Button>
      }
    >
      {capability.data?.can_create === false && (
        <p role="status" className="rounded-lg border bg-muted/30 p-4 text-sm">
          {t('unconfigured')}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label={t('search')}
            placeholder={t('search')}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="pl-9"
          />
        </div>
        <select
          aria-label={t('status')}
          className="h-9 rounded-md border bg-background px-3 text-sm"
          value={search.status}
          onChange={(e) =>
            void navigate({
              to: '/compile',
              search: { ...search, status: e.target.value },
            })
          }
        >
          <option value="">{t('all')}</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {t(`statuses.${status}`)}
            </option>
          ))}
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
      {hasNew && (
        <Button
          variant="outline"
          className="self-start"
          onClick={() => void query.refetch()}
        >
          {t('newTasks')}
        </Button>
      )}
      {query.isLoading ? (
        <CompileLoading />
      ) : query.isError ? (
        <CompileError error={query.error} retry={() => void query.refetch()} />
      ) : null}
      {!query.isLoading && !query.isError && !tasks.length && (
        <div className="grid justify-items-center gap-3 rounded-xl border border-dashed py-16 text-center">
          <SparklesIcon className="size-8 text-muted-foreground" />
          <h2 className="font-medium">
            {t(search.q || search.status ? 'emptyFiltered' : 'empty')}
          </h2>
          <p className="text-sm text-muted-foreground">{t('description')}</p>
        </div>
      )}
      {!!tasks.length && (
        <div className="overflow-hidden rounded-xl border">
          <div className="hidden grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_150px_16px] gap-5 border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground lg:grid">
            <span>{t('task')}</span>
            <span>{t('materials')}</span>
            <span>{t('status')}</span>
            <span>{t('createdAt')}</span>
            <span />
          </div>
          {tasks.map((task) => {
            const request = task.meta?.request
            const leaf = (uri: string) =>
              uri.split('/').filter(Boolean).pop() || uri
            const date = task.created_at
              ? new Date(Number(task.created_at) * 1000)
              : null
            return (
              <Link
                key={task.task_id}
                to="/compile/tasks/$taskId"
                params={{ taskId: task.task_id }}
                state={{
                  compileListOrigin: {
                    scope: identityScopeKey,
                    index: location.state.__TSR_index,
                    search,
                  },
                }}
                className="group grid gap-4 border-b bg-card px-5 py-5 transition-colors last:border-b-0 hover:bg-muted/30 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-[-2px] lg:grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_150px_16px] lg:items-center lg:gap-5"
              >
                <div className="min-w-0 space-y-2">
                  <p
                    className="break-words text-sm font-semibold [overflow-wrap:anywhere]"
                    title={request?.skill}
                  >
                    {request ? leaf(request.skill) : t('title')}
                  </p>
                  <div
                    className="flex items-start gap-2 text-xs text-muted-foreground"
                    title={request?.to}
                  >
                    <FolderOutput className="mt-0.5 size-3.5 shrink-0" />
                    <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                      {request ? leaf(request.to) : '—'}
                    </span>
                  </div>
                  <p
                    className="font-mono text-[11px] text-muted-foreground/70"
                    title={task.task_id}
                  >
                    {task.task_id.length > 24
                      ? `${task.task_id.slice(0, 12)}…${task.task_id.slice(-6)}`
                      : task.task_id}
                  </p>
                </div>
                <div className="min-w-0 space-y-2">
                  <p className="text-xs text-muted-foreground lg:hidden">
                    {t('materials')}
                  </p>
                  {request?.from.length
                    ? request.from.slice(0, 2).map((uri) => (
                        <div
                          key={uri}
                          title={uri}
                          className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"
                        >
                          <FolderInput className="mt-0.5 size-3.5 shrink-0" />
                          <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                            {leaf(uri)}
                          </span>
                        </div>
                      ))
                    : '—'}
                  {request && request.from.length > 2 && (
                    <p className="text-xs text-muted-foreground">
                      {t('moreSources', { count: request.from.length - 2 })}
                    </p>
                  )}
                </div>
                <div className="min-w-0 space-y-2">
                  <CompileStatus status={task.status} />
                  {task.stage && (
                    <p
                      className="break-words text-xs text-muted-foreground"
                      title={task.stage}
                    >
                      {t(`stages.${task.stage.replace(/^compile:\s*/, '')}`, {
                        defaultValue: task.stage,
                      })}
                    </p>
                  )}
                </div>
                <time
                  dateTime={date?.toISOString()}
                  className="text-xs leading-5 text-muted-foreground"
                >
                  {date ? (
                    <>
                      <span className="block">
                        {date.toLocaleDateString(i18n.language)}
                      </span>
                      <span className="block text-muted-foreground/70">
                        {date.toLocaleTimeString(i18n.language)}
                      </span>
                    </>
                  ) : (
                    '—'
                  )}
                </time>
                <ChevronRight className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 lg:block" />
              </Link>
            )
          })}
        </div>
      )}
      {query.hasNextPage && (
        <Button
          variant="outline"
          className="self-center"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t(query.isFetchingNextPage ? 'loading' : 'more')}
        </Button>
      )}
    </CompileShell>
  )
}
