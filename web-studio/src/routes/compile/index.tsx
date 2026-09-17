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
import { ContextLink, useCompileSkillName } from './-components/context-link'
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
  const skillName = useCompileSkillName()
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
          <div className="hidden grid-cols-[180px_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_100px_110px] gap-5 border-b bg-muted/15 px-5 py-3 text-xs font-medium text-muted-foreground xl:grid">
            <span>{t('taskId')}</span>
            <span>{t('skillColumn')}</span>
            <span>{t('materials')}</span>
            <span>{t('outputDirectory')}</span>
            <span>{t('status')}</span>
            <span>{t('createdAt')}</span>
          </div>
          {tasks.map((task) => {
            const request = task.meta?.request
            const date = task.created_at
              ? new Date(Number(task.created_at) * 1000)
              : null
            return (
              <div
                key={task.task_id}
                className="grid gap-4 border-b bg-card/40 px-5 py-4 transition-colors last:border-b-0 hover:bg-muted/30 xl:grid-cols-[180px_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_100px_110px] xl:items-start xl:gap-5"
              >
                <div className="min-w-0 space-y-2">
                  <p className="text-xs text-muted-foreground xl:hidden">
                    {t('taskId')}
                  </p>
                  <Link
                    to="/compile/tasks/$taskId"
                    params={{ taskId: task.task_id }}
                    state={{
                      compileListOrigin: {
                        scope: identityScopeKey,
                        index: location.state.__TSR_index,
                        search,
                      },
                    }}
                    aria-label={`${t('detailTitle')}: ${task.task_id}`}
                    title={`${t('detailTitle')}: ${task.task_id}`}
                    className="group inline-flex min-w-0 items-center gap-1 rounded-sm py-0.5 text-xs font-medium text-foreground transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    <span className="truncate font-mono">
                      {task.task_id.length > 22
                        ? `${task.task_id.slice(0, 10)}…${task.task_id.slice(-6)}`
                        : task.task_id}
                    </span>
                    <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                  </Link>
                </div>
                <div className="min-w-0 space-y-2">
                  <p className="text-xs text-muted-foreground xl:hidden">
                    {t('skillColumn')}
                  </p>
                  <p
                    className="break-words text-sm font-medium [overflow-wrap:anywhere]"
                    title={request?.skill}
                  >
                    {request ? (
                      <ContextLink uri={request.skill}>
                        {skillName(request.skill)}
                      </ContextLink>
                    ) : (
                      '—'
                    )}
                  </p>
                </div>
                <div className="min-w-0 space-y-2">
                  <p className="text-xs text-muted-foreground xl:hidden">
                    {t('materials')}
                  </p>
                  {request?.from.length
                    ? request.from.map((uri) => (
                        <div
                          key={uri}
                          title={uri}
                          className="flex items-start gap-2 text-sm leading-5"
                        >
                          <FolderInput className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60" />
                          <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                            <ContextLink uri={uri} resolveFile />
                          </span>
                        </div>
                      ))
                    : '—'}
                </div>
                <div className="min-w-0 space-y-2">
                  <p className="text-xs text-muted-foreground xl:hidden">
                    {t('outputDirectory')}
                  </p>
                  <div className="flex items-start gap-2 text-sm leading-5">
                    <FolderOutput className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60" />
                    {request ? <ContextLink uri={request.to} /> : '—'}
                  </div>
                </div>
                <div className="min-w-0 space-y-2">
                  <CompileStatus status={task.status} />
                  {task.stage && task.status !== 'completed' && (
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
              </div>
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
