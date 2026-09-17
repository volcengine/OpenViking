import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  Copy,
  FolderInput,
  FolderOutput,
  WandSparkles,
  Activity,
} from 'lucide-react'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { TaskExecutionEvents } from '#/routes/tasks/-components/task-execution-events'
import {
  CompileError,
  CompileLoading,
  CompileShell,
  CompileStatus,
} from '../-components/shared'
import { ContextLink, useCompileSkillName } from '../-components/context-link'
import { active, cancelCompile, fetchCompileTask } from '../-lib/api'

const TABS = ['overview', 'events', 'result'] as const

export const Route = createFileRoute('/compile/tasks/$taskId')({
  component: CompileDetail,
})
function CompileDetail() {
  const skillName = useCompileSkillName()
  const { taskId } = Route.useParams(),
    { t, i18n } = useTranslation('compile')
  const { identityScopeKey, connectionRole } = useAppConnection(),
    client = useQueryClient()
  const [tab, setTab] = useState<'overview' | 'events' | 'result'>('overview')
  const key = ['compile-task', identityScopeKey, taskId]
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchCompileTask(taskId, signal),
    refetchInterval: (q) => (active(q.state.data?.status) ? 5000 : false),
  })
  const stop = useMutation({
    mutationFn: () => cancelCompile(taskId),
    onSuccess: (task) => {
      client.setQueryData(key, task)
      void client.invalidateQueries({
        queryKey: ['compile-list', identityScopeKey],
      })
    },
  })
  const task = query.data,
    request = task?.meta?.request
  const previousStatus = useRef<{ id: string; status?: string } | null>(null)
  useEffect(() => {
    if (
      previousStatus.current?.id === taskId &&
      active(previousStatus.current.status) &&
      task?.status === 'completed'
    ) {
      toast.success(t('statuses.completed'), {
        action: { label: t('result'), onClick: () => setTab('result') },
      })
    }
    if (task) previousStatus.current = { id: taskId, status: task.status }
  }, [taskId, task, t])
  return (
    <CompileShell
      title={t('detailTitle')}
      actions={
        request && (
          <Link
            to="/compile/new"
            search={{ fromTask: taskId }}
            className="rounded-md border px-4 py-2 text-sm hover:bg-muted"
          >
            {t('rerun')}
          </Link>
        )
      }
    >
      {query.isLoading && <CompileLoading />}
      {query.isError && (
        <CompileError
          error={task ? t('stale') : query.error}
          retry={() => void query.refetch()}
        />
      )}
      {task && task.task_type !== 'compile' && (
        <CompileError error={t('invalidTask')} />
      )}
      {task?.task_type === 'compile' && (
        <>
          <section className="rounded-xl border bg-card p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-3">
              <CompileStatus status={task.status} />
              <button
                title={taskId}
                aria-label={t('copyTaskId')}
                className="inline-flex min-w-0 items-center gap-2 rounded px-2 py-1 font-mono text-xs text-muted-foreground hover:bg-muted"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(taskId)
                    .then(() => toast.success(t('copied')))
                    .catch(() => toast.error(t('failedLoad')))
                }
              >
                <span>{taskId.slice(0, 16)}…</span>
                <Copy className="size-3" />
              </button>
              {active(task.status) && (
                <Button
                  variant="outline"
                  className="ml-auto"
                  title={
                    connectionRole === 'root' ? t('rootCancel') : undefined
                  }
                  disabled={
                    connectionRole === 'root' ||
                    task.status === 'cancelling' ||
                    stop.isPending
                  }
                  onClick={() => {
                    if (window.confirm(t('stopConfirm'))) stop.mutate()
                  }}
                >
                  {t('stop')}
                </Button>
              )}
            </div>
            <div className="mt-5 grid gap-4 border-t pt-5 text-sm sm:grid-cols-2">
              {task.stage && (
                <div>
                  <p className="mb-2 text-xs text-muted-foreground">
                    {t('stage')}
                  </p>
                  <p className="flex items-center gap-2 font-medium">
                    <Activity className="size-4 text-primary" />
                    {t(`stages.${task.stage.replace(/^compile:\s*/, '')}`, {
                      defaultValue: task.stage,
                    })}
                  </p>
                </div>
              )}
              <div>
                <p className="mb-2 text-xs text-muted-foreground">
                  {t('updated')}
                </p>
                <p className="font-medium">
                  {task.updated_at
                    ? new Date(Number(task.updated_at) * 1000).toLocaleString(
                        i18n.language,
                      )
                    : '—'}
                </p>
              </div>
            </div>
            {active(task.status) && (
              <p className="mt-4 text-xs text-muted-foreground">
                {t('runningHint')}
              </p>
            )}
          </section>
          {stop.isError && <CompileError error={stop.error} />}
          {task.error && <CompileError error={task.error} />}
          <div role="tablist" className="flex gap-2 border-b pb-3">
            {TABS.map((item) => (
              <Button
                key={item}
                role="tab"
                aria-selected={tab === item}
                variant={tab === item ? 'secondary' : 'ghost'}
                onClick={() => setTab(item)}
              >
                {t(item)}
              </Button>
            ))}
          </div>
          <div role="tabpanel">
            {tab === 'overview' &&
              (request ? (
                <dl className="grid gap-4 text-sm md:grid-cols-2">
                  <div className="rounded-xl border bg-card p-5 md:col-span-2">
                    <dt className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <WandSparkles className="size-4" />
                      {t('skill')}
                    </dt>
                    <dd>
                      <p className="mb-2 break-all font-medium">
                        <ContextLink uri={request.skill}>
                          {skillName(request.skill)}
                        </ContextLink>
                      </p>
                      <p className="break-all font-mono text-xs text-muted-foreground">
                        {request.skill}
                      </p>
                    </dd>
                  </div>
                  <div className="min-w-0 rounded-xl border bg-card p-5">
                    <dt className="mb-4 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <FolderInput className="size-4" />
                      {t('sources')}
                      <span className="ml-auto rounded bg-muted px-2 py-0.5">
                        {request.from.length}
                      </span>
                    </dt>
                    <dd className="space-y-4">
                      {request.from.map((uri) => (
                        <div key={uri}>
                          <p className="mb-1 break-all font-medium">
                            <ContextLink uri={uri} resolveFile />
                          </p>
                          <p className="break-all font-mono text-xs leading-5 text-muted-foreground">
                            {uri}
                          </p>
                        </div>
                      ))}
                    </dd>
                  </div>
                  <div className="min-w-0 rounded-xl border bg-card p-5">
                    <dt className="mb-4 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <FolderOutput className="size-4" />
                      {t('target')}
                    </dt>
                    <dd>
                      <p className="mb-1 break-all font-medium">
                        <ContextLink uri={request.to} />
                      </p>
                      <p className="break-all font-mono text-xs leading-5 text-muted-foreground">
                        {request.to}
                      </p>
                    </dd>
                  </div>
                  {request.instruction && (
                    <div className="rounded-xl border bg-card p-5 md:col-span-2">
                      <dt className="mb-2 font-medium">{t('instruction')}</dt>
                      <dd className="whitespace-pre-wrap">
                        {request.instruction}
                      </dd>
                    </div>
                  )}
                  {request.args && (
                    <details className="rounded-xl border p-5 md:col-span-2">
                      <summary>{t('advanced')}</summary>
                      <pre className="mt-3 overflow-auto rounded-lg bg-muted p-4 text-xs">
                        {JSON.stringify(request.args, null, 2)}
                      </pre>
                    </details>
                  )}
                </dl>
              ) : (
                <p>{t('missingRequest')}</p>
              ))}
            {tab === 'events' && <TaskExecutionEvents task={task} />}
            {tab === 'result' && (
              <div className="space-y-5">
                {request && (
                  <Link
                    to="/playground"
                    search={{ uri: request.to }}
                    className="inline-flex rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                  >
                    {t('output')}
                  </Link>
                )}
                {task.result == null ? (
                  <p className="text-sm text-muted-foreground">
                    {t('noResult')}
                  </p>
                ) : (
                  <details open>
                    <summary className="text-sm font-medium">
                      {t('raw')}
                    </summary>
                    <pre className="mt-3 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/30 p-4 text-xs">
                      {JSON.stringify(task.result, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </CompileShell>
  )
}
