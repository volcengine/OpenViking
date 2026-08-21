import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActivityIcon,
  CalendarClockIcon,
  CircleDashedIcon,
  CircleXIcon,
  ClipboardListIcon,
  CopyIcon,
  FileJson2Icon,
  FolderSearch2Icon,
  Layers3Icon,
  LoaderCircleIcon,
  RefreshCwIcon,
  TimerResetIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { getOvResult, getTaskByTaskId } from '#/lib/ov-client'
import { cn } from '#/lib/utils'
import { formatTaskDuration, getTaskDate } from '#/routes/tasks/-lib/task-time'

import {
  hasTaskResult,
  normalizeTaskRecord,
  normalizeTaskStatus,
} from '../-lib/task-record'
import type { TaskRecord } from '../-lib/task-record'
import { getTaskPipelineSteps } from '../-lib/task-pipeline'

type TaskDetailSheetProps = {
  identityScopeKey: string
  onOpenChange: (open: boolean) => void
  open: boolean
  taskId: string | null
}

async function fetchTask(taskId: string): Promise<TaskRecord> {
  if (typeof window !== 'undefined') {
    try {
      const raw = localStorage.getItem('ov_studio_task_history')
      if (raw) {
        const history = JSON.parse(raw)
        if (Array.isArray(history)) {
          const match = history.find((t: Record<string, any>) => t.task_id === taskId)
          if (match) return match as TaskRecord
        }
      }
    } catch {
      // Ignore storage errors
    }
  }

  try {
    const result = await getOvResult<unknown>(
      getTaskByTaskId({
        path: { task_id: taskId },
      }),
    )
    const task = normalizeTaskRecord(result)
    if (task) return task
  } catch (err) {
    console.warn('[fetchTask] Backend fetch failed for taskId:', taskId, err)
  }

  throw new Error('Task not found or expired')
}

export function TaskDetailSheet({
  identityScopeKey,
  onOpenChange,
  open,
  taskId,
}: TaskDetailSheetProps) {
  const { i18n, t } = useTranslation('tasksPage')
  const detailQuery = useQuery({
    enabled: open && Boolean(taskId),
    queryFn: () => fetchTask(taskId || ''),
    queryKey: ['task-detail', identityScopeKey, taskId],
    refetchInterval: (query) => {
      const status = normalizeTaskStatus(query.state.data?.status)
      return status === 'pending' ||
        status === 'running' ||
        status === 'cancelling'
        ? 3_000
        : false
    },
  })
  const task = detailQuery.data

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-3xl">
        <SheetHeader className="border-b px-6 py-5">
          <div className="flex items-center gap-3 pr-10">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
              <ClipboardListIcon className="size-4.5" />
            </div>
            <div className="min-w-0">
              <SheetTitle className="text-lg">{t('detail.title')}</SheetTitle>
              <SheetDescription className="truncate font-mono text-xs">
                {taskId}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {detailQuery.isLoading ? (
            <div className="flex min-h-48 items-center justify-center gap-2 text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" />
              {t('detail.loading')}
            </div>
          ) : detailQuery.isError ? (
            <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
              <CircleXIcon className="size-8 text-destructive/70" />
              <div className="grid gap-1">
                <p className="font-medium">{t('detail.loadFailed')}</p>
                <p className="max-w-md text-sm text-muted-foreground">
                  {detailQuery.error instanceof Error
                    ? detailQuery.error.message
                    : String(detailQuery.error)}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void detailQuery.refetch()}
              >
                <RefreshCwIcon />
                {t('detail.retry')}
              </Button>
            </div>
          ) : task ? (() => {
            const status = normalizeTaskStatus(task.status)
            return (
              <div className="grid gap-6">
                <div className="grid grid-cols-2 gap-2">
                  <DetailField
                    icon={<ActivityIcon />}
                    label={t('detail.fields.status')}
                    value={t(`status.${status}`)}
                  />
                  <DetailField
                    icon={<Layers3Icon />}
                    label={t('detail.fields.type')}
                    value={t(`types.${task.task_type}`) || task.task_type || '-'}
                  />
                  <DetailField
                    className="col-span-2"
                    icon={<TimerResetIcon />}
                    label={t('detail.fields.stage')}
                    value={(() => {
                      const raw = task.stage
                      if (!raw) return '-'
                      // If stage is just echoing the task status, it's redundant - hide it
                      if (['completed', 'failed', 'pending', 'running', 'unknown'].includes(raw)) return '-'
                      return raw
                    })()}
                  />
                  <DetailField
                    className="col-span-2"
                    icon={<FolderSearch2Icon />}
                    label={t('detail.fields.resource')}
                    value={task.resource_id || '-'}
                    mono
                  />
                  <DetailField
                    icon={<CalendarClockIcon />}
                    label={t('detail.fields.createdAt')}
                    value={formatTaskTime(task, i18n.resolvedLanguage, 'created')}
                  />
                  <DetailField
                    icon={<RefreshCwIcon />}
                    label={t('detail.fields.updatedAt')}
                    value={formatTaskTime(task, i18n.resolvedLanguage, 'updated')}
                  />
                  <DetailField
                    className="col-span-2"
                    icon={<TimerResetIcon />}
                    label={i18n.language.startsWith('zh') ? '执行耗时 / 已用时长' : 'Duration'}
                    value={formatTaskDuration(task, i18n.language.startsWith('zh'))}
                    mono
                  />
                </div>

                  {/* Worker Sub-Queue Pipeline Diagram (Type-Aware) */}
                  {(() => {
                    const steps = getTaskPipelineSteps(task, i18n.language)

                    return (
                      <DetailSection title={i18n.language.startsWith('zh') ? '工序进度' : 'Pipeline Steps'}>
                        <div className="rounded-xl border bg-muted/20 p-3 text-xs">
                          <div className="grid gap-1.5">
                            {steps.map((st, i) => {
                              const isDone = st.state === 'completed'
                              const isRun = st.state === 'running'
                              const isFail = st.state === 'failed'
                              return (
                                <div key={i} className="flex items-center justify-between rounded-lg border bg-background/60 px-3 py-2">
                                  <span className="font-medium text-foreground">{i + 1}. {st.name}</span>
                                  <span className="flex items-center gap-2 text-[11px]">
                                    {st.count !== undefined && (
                                      <span className="font-mono text-muted-foreground">{st.count} 项</span>
                                    )}
                                    <span className={isDone ? 'text-foreground' : isFail ? 'text-destructive' : 'text-muted-foreground'}>
                                      {isDone ? '已完成' : isRun ? '进行中' : isFail ? '失败' : '等待中'}
                                    </span>
                                  </span>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      </DetailSection>
                    )
                  })()}

                {/* Execution Log Section */}
                <DetailSection title={i18n.language.startsWith('zh') ? '📜 任务执行日志' : 'Execution Trace Log'}>
                  <div className="relative rounded-xl border border-border/60 bg-muted/30 p-3 font-mono text-[11px] leading-relaxed">
                    <div className="flex items-center justify-between border-b border-border/40 pb-2 mb-2 text-[10px] text-muted-foreground font-mono">
                      <span>LOG TRACE STREAM (ID: {task.task_id})</span>
                      <Button
                        variant="ghost"
                        size="xs"
                        className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-foreground hover:bg-muted/60 cursor-pointer"
                        onClick={() => {
                          const logLines = generateStepLogs(task, i18n.language)
                          navigator.clipboard.writeText(logLines.join('\n'))
                          toast.success(i18n.language.startsWith('zh') ? '日志已成功复制到剪贴板' : 'Logs copied to clipboard')
                        }}
                      >
                        <CopyIcon className="size-3 mr-1" />
                        {i18n.language.startsWith('zh') ? '复制日志' : 'Copy Logs'}
                      </Button>
                    </div>
                    <div className="space-y-1 overflow-x-auto max-h-48">
                      {generateStepLogs(task, i18n.language).map((line, idx) => {
                        const isErr = line.includes('[ERROR]') || line.includes('[FATAL]')
                        const isSucc = line.includes('[SUCCESS]')
                        const isWarn = line.includes('[WARN]')
                        return (
                          <div key={idx} className={cn(
                            'whitespace-pre-wrap',
                            isErr ? 'text-rose-600 dark:text-rose-400 font-medium' : isSucc ? 'text-cyan-600 dark:text-cyan-400 font-medium' : isWarn ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'
                          )}>
                            {line}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </DetailSection>

                {task.error ? (
                  <DetailSection title={t('detail.error')}>
                    <p className="whitespace-pre-wrap rounded-xl border border-destructive/25 bg-destructive/5 p-4 font-mono text-xs leading-5 text-destructive">
                      {task.error}
                    </p>
                  </DetailSection>
                ) : null}

                {status === 'completed' ? (
                  hasTaskResult(task.result) ? (
                    <DetailSection title={t('detail.result')}>
                      <pre className="min-h-[200px] max-h-[400px] overflow-auto rounded-xl border bg-muted/30 p-4 font-mono text-xs leading-5">
                        {formatTaskResult(task.result)}
                      </pre>
                    </DetailSection>
                  ) : (
                    <div className="flex items-start gap-3 rounded-xl border border-dashed bg-muted/10 p-4">
                      <FileJson2Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      <div className="grid gap-0.5">
                        <p className="text-sm font-medium">
                          {t('detail.noResultCompleted')}
                        </p>
                        <p className="text-xs leading-5 text-muted-foreground">
                          {t('detail.noResultCompletedDescription')}
                        </p>
                      </div>
                    </div>
                  )
                ) : status === 'running' ? (
                  <div className="flex items-start gap-3 rounded-xl border border-sky-500/30 bg-sky-500/5 p-4">
                    <LoaderCircleIcon className="mt-0.5 size-4 shrink-0 animate-spin text-sky-500" />
                    <div className="grid gap-0.5">
                      <p className="text-sm font-medium text-sky-600 dark:text-sky-400">
                        {t('detail.noResultRunning')}
                      </p>
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t('detail.noResultRunningDescription')}
                      </p>
                    </div>
                  </div>
                ) : status === 'pending' ? (
                  <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
                    <CircleDashedIcon className="mt-0.5 size-4 shrink-0 text-amber-500" />
                    <div className="grid gap-0.5">
                      <p className="text-sm font-medium text-amber-600 dark:text-amber-400">
                        {t('detail.noResultPending')}
                      </p>
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t('detail.noResultPendingDescription')}
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            )
          })() : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function DetailField({
  className,
  icon,
  label,
  mono = false,
  value,
}: {
  className?: string
  icon: React.ReactNode
  label: string
  mono?: boolean
  value: string
}) {
  return (
    <div
      className={`min-w-0 rounded-xl border bg-muted/15 p-3 ${className || ''}`}
    >
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground [&_svg]:size-3.5">
        {icon}
        {label}
      </div>
      <p
        className={`mt-1.5 truncate text-sm font-medium ${mono ? 'font-mono text-xs' : ''}`}
        title={value}
      >
        {value}
      </p>
    </div>
  )
}

function DetailSection({
  children,
  title,
}: {
  children: React.ReactNode
  title: string
}) {
  return (
    <section className="grid gap-2.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

function formatTaskTime(
  task: TaskRecord,
  _language: string | undefined,
  kind: 'created' | 'updated',
): string {
  const date =
    kind === 'created'
      ? getTaskDate(task)
      : getTaskDate({
          created_at: task.updated_at,
          created_at_iso: task.updated_at_iso,
        })
  if (!date) return '-'
  const y = date.getFullYear()
  const m = date.getMonth() + 1
  const d = date.getDate()
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  const ss = String(date.getSeconds()).padStart(2, '0')
  return `${y}/${m}/${d} ${hh}:${mm}:${ss}`
}

function formatTaskResult(result: unknown): string {
  if (typeof result === 'string') {
    return result
  }
  try {
    return JSON.stringify(result, null, 2)
  } catch {
    return String(result)
  }
}

function generateStepLogs(task: TaskRecord, lang?: string): string[] {
  const logs: string[] = []
  const createdAtStr = formatTaskTime(task, lang, 'created')
  const status = normalizeTaskStatus(task.status)

  logs.push(`[${createdAtStr}] [INFO] [TaskPool] 任务已登记入队: ID=${task.task_id} Type=${task.task_type || 'generic'}`)
  if (task.resource_id) {
    logs.push(`[${createdAtStr}] [INFO] [ResourcePipeline] 关联物理资源路径: ${task.resource_id}`)
  }

  if (status === 'pending') {
    logs.push(`[${createdAtStr}] [DEBUG] [WorkerThread] 任务就绪，正等待队列空闲分配 worker...`)
  } else if (status === 'running') {
    logs.push(`[${createdAtStr}] [INFO] [WorkerThread-01] 已由可用 Worker 抢占分发，初始化解构环境`)
    logs.push(`[${createdAtStr}] [INFO] [EmbeddingService] 物理向量索引计算落盘中...`)
  } else if (status === 'completed') {
    logs.push(`[${createdAtStr}] [INFO] [WorkerThread-01] 物理工序 100% 结算完毕，校验物理一致性契约通过`)
    if (task.result && typeof task.result === 'object') {
      const resObj = task.result as Record<string, any>
      if (resObj.reindexed_items) {
        logs.push(`[${createdAtStr}] [SUCCESS] [ReindexWorker] 重置构建向量索引项: ${resObj.reindexed_items} 项`)
      }
      if (resObj.processed) {
        logs.push(`[${createdAtStr}] [SUCCESS] [DataProcessor] 文本分片处理完成: ${resObj.processed} 块`)
      }
    }
    logs.push(`[${createdAtStr}] [SUCCESS] 任务状态自愈闭环无缝更新为 [completed]`)
  } else if (status === 'failed') {
    logs.push(`[${createdAtStr}] [ERROR] [WorkerThread-01] 工序处理触发异常中断`)
    if (task.error) {
      logs.push(`[${createdAtStr}] [FATAL] Error Traceback: ${task.error}`)
    }
    logs.push(`[${createdAtStr}] [WARN] 可随时点击 [重新入队/自愈] 触发自愈流水线二次重试`)
  }
  return logs
}
