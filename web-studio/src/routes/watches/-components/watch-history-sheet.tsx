import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleXIcon,
  ClipboardListIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import {
  getTaskDate,
  isActiveTaskStatus,
  normalizeTaskStatus,
} from '#/routes/tasks/task-model'
import type { TaskRecord } from '#/routes/tasks/task-model'

import { fetchWatchProcessingHistory } from '../-lib/api'
import type { WatchTask } from '../-lib/api'

type WatchHistorySheetProps = {
  identityScopeKey: string
  onOpenChange: (open: boolean) => void
  open: boolean
  watch: WatchTask | null
}

export function WatchHistorySheet({
  identityScopeKey,
  onOpenChange,
  open,
  watch,
}: WatchHistorySheetProps) {
  const { i18n, t } = useTranslation('watchesPage')
  const { t: taskT } = useTranslation('tasksPage')
  const historyQuery = useQuery({
    enabled: open && Boolean(watch),
    queryFn: () => fetchWatchProcessingHistory(watch?.toUri || ''),
    queryKey: ['watch-processing-history', identityScopeKey, watch?.toUri],
    refetchInterval: (query) =>
      query.state.data?.some((task) => isActiveTaskStatus(task.status))
        ? 3_000
        : false,
  })
  const tasks = historyQuery.data ?? []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-5">
          <div className="flex items-start justify-between gap-3 pr-10">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
                <ClipboardListIcon className="size-4.5" />
              </div>
              <div className="min-w-0">
                <SheetTitle className="text-lg">
                  {t('history.title')}
                </SheetTitle>
                <SheetDescription
                  className="truncate font-mono text-xs"
                  title={watch?.toUri}
                >
                  {watch?.toUri}
                </SheetDescription>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={historyQuery.isFetching}
              onClick={() => void historyQuery.refetch()}
            >
              <RefreshCwIcon
                className={historyQuery.isFetching ? 'animate-spin' : undefined}
              />
              {t('refresh')}
            </Button>
          </div>
          <p className="pt-2 text-xs leading-5 text-muted-foreground">
            {t('history.description')}
          </p>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {historyQuery.isLoading ? (
            <HistoryState icon={<LoaderCircleIcon className="animate-spin" />}>
              {t('history.loading')}
            </HistoryState>
          ) : historyQuery.isError ? (
            <HistoryState icon={<CircleXIcon className="text-destructive" />}>
              <span>{t('history.loadFailed')}</span>
              <span className="max-w-md text-xs text-muted-foreground">
                {historyQuery.error instanceof Error
                  ? historyQuery.error.message
                  : String(historyQuery.error)}
              </span>
            </HistoryState>
          ) : tasks.length === 0 ? (
            <HistoryState icon={<ClipboardListIcon />}>
              <span>{t('history.empty')}</span>
              <span className="text-xs text-muted-foreground">
                {t('history.emptyDescription')}
              </span>
            </HistoryState>
          ) : (
            <div className="grid gap-3">
              {tasks.map((task, index) => {
                const status = normalizeTaskStatus(task.status)
                return (
                  <article
                    key={task.task_id || String(index)}
                    className="grid gap-3 rounded-xl border bg-card/50 p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {formatTaskTime(task, i18n.resolvedLanguage)}
                        </p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                          {task.task_id || '-'}
                        </p>
                      </div>
                      <TaskStatusBadge
                        task={task}
                        label={taskT(`status.${status}`)}
                      />
                    </div>
                    <div className="grid gap-1 text-xs">
                      <span className="text-muted-foreground">
                        {t('history.stage')}
                      </span>
                      <span>{task.stage || '-'}</span>
                    </div>
                    {task.error ? (
                      <p className="line-clamp-3 rounded-lg bg-destructive/5 px-3 py-2 font-mono text-xs leading-5 text-destructive">
                        {task.error}
                      </p>
                    ) : null}
                  </article>
                )
              })}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function TaskStatusBadge({ task, label }: { task: TaskRecord; label: string }) {
  const status = normalizeTaskStatus(task.status)
  const Icon =
    status === 'completed'
      ? CheckCircle2Icon
      : status === 'failed' || status === 'cancelled'
        ? CircleXIcon
        : status === 'running' || status === 'cancelling'
          ? LoaderCircleIcon
          : CircleDashedIcon

  return (
    <Badge
      variant={status === 'failed' ? 'destructive' : 'outline'}
      className="font-normal"
    >
      <Icon
        className={
          status === 'running' || status === 'cancelling'
            ? 'animate-spin'
            : undefined
        }
      />
      {label}
    </Badge>
  )
}

function HistoryState({
  children,
  icon,
}: {
  children: ReactNode
  icon: ReactNode
}) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center gap-2 text-center text-sm [&_svg]:size-7 [&_svg]:text-muted-foreground">
      {icon}
      {children}
    </div>
  )
}

function formatTaskTime(
  task: TaskRecord,
  language: string | undefined,
): string {
  const date = getTaskDate(task)
  if (!date) return '-'
  return new Intl.DateTimeFormat(language, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(date)
}
