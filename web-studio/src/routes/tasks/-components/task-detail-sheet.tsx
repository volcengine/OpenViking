import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActivityIcon,
  CalendarClockIcon,
  CircleXIcon,
  ClipboardListIcon,
  FileJson2Icon,
  FolderSearch2Icon,
  Layers3Icon,
  LoaderCircleIcon,
  RefreshCwIcon,
  TimerResetIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { getOvResult, getTaskByTaskId } from '#/lib/ov-client'
import { getTaskDate } from '#/routes/tasks/-lib/task-time'

import {
  hasTaskResult,
  normalizeTaskRecord,
  normalizeTaskStatus,
} from '../-lib/task-record'
import type { TaskRecord } from '../-lib/task-record'

type TaskDetailSheetProps = {
  identityScopeKey: string
  onOpenChange: (open: boolean) => void
  open: boolean
  taskId: string | null
}

async function fetchTask(taskId: string): Promise<TaskRecord> {
  const result = await getOvResult<unknown>(
    getTaskByTaskId({
      path: { task_id: taskId },
    }),
  )
  const task = normalizeTaskRecord(result)
  if (!task) {
    throw new Error('Invalid task detail response')
  }
  return task
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
      return status === 'pending' || status === 'running' ? 3_000 : false
    },
  })
  const task = detailQuery.data

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 sm:max-w-xl">
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
          ) : task ? (
            <div className="grid gap-6">
              <div className="grid grid-cols-2 gap-2">
                <DetailField
                  icon={<ActivityIcon />}
                  label={t('detail.fields.status')}
                  value={t(`status.${normalizeTaskStatus(task.status)}`)}
                />
                <DetailField
                  icon={<Layers3Icon />}
                  label={t('detail.fields.type')}
                  value={task.task_type || '-'}
                />
                <DetailField
                  className="col-span-2"
                  icon={<TimerResetIcon />}
                  label={t('detail.fields.stage')}
                  value={task.stage || '-'}
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
              </div>

              {task.error ? (
                <DetailSection title={t('detail.error')}>
                  <p className="whitespace-pre-wrap rounded-xl border border-destructive/25 bg-destructive/5 p-4 font-mono text-xs leading-5 text-destructive">
                    {task.error}
                  </p>
                </DetailSection>
              ) : null}

              {hasTaskResult(task.result) ? (
                <DetailSection title={t('detail.result')}>
                  <pre className="max-h-96 overflow-auto rounded-xl border bg-muted/30 p-4 font-mono text-xs leading-5">
                    {formatTaskResult(task.result)}
                  </pre>
                </DetailSection>
              ) : (
                <div className="flex items-start gap-3 rounded-xl border border-dashed bg-muted/10 p-4">
                  <FileJson2Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="grid gap-0.5">
                    <p className="text-sm font-medium">
                      {t('detail.noResult')}
                    </p>
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t(
                        normalizeTaskStatus(task.status) === 'failed'
                          ? 'detail.noResultFailedDescription'
                          : 'detail.noResultDescription',
                      )}
                    </p>
                  </div>
                </div>
              )}
            </div>
          ) : null}
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
  language: string | undefined,
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
  return new Intl.DateTimeFormat(language, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(date)
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
