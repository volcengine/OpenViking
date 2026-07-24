import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import {
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleXIcon,
  ChevronRightIcon,
  ClipboardListIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card } from '#/components/ui/card'
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from '#/components/ui/pagination'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { useAppConnection } from '#/hooks/use-app-connection'
import { getOvResult, getTasks } from '#/lib/ov-client'
import { cn } from '#/lib/utils'
import { TaskDetailSheet } from '#/routes/tasks/-components/task-detail-sheet'
import {
  normalizeTasks,
  normalizeTaskStatus,
} from '#/routes/tasks/-lib/task-record'
import type { TaskRecord, TaskStatus } from '#/routes/tasks/-lib/task-record'
import { getTaskDate } from '#/routes/tasks/-lib/task-time'

export const Route = createFileRoute('/tasks')({
  component: TasksRoute,
})

type TaskStatusFilter = Exclude<TaskStatus, 'unknown'> | 'all'

type TaskTypeFilter =
  | 'add_resource'
  | 'add_skill'
  | 'admin_reindex'
  | 'connector_import'
  | 'legacy_cleanup'
  | 'legacy_migration'
  | 'session_commit'
  | 'snapshot_restore_reindex'
  | 'all'

const DEFAULT_PAGE_SIZE = 20
const MAX_TASKS = 200
const PAGE_SIZE_OPTIONS = [20, 50, 100] as const
const TASK_TYPE_OPTIONS: Exclude<TaskTypeFilter, 'all'>[] = [
  'session_commit',
  'add_resource',
  'add_skill',
  'connector_import',
  'admin_reindex',
  'snapshot_restore_reindex',
  'legacy_migration',
  'legacy_cleanup',
]
const TASK_STATUS_OPTIONS: Exclude<TaskStatusFilter, 'all'>[] = [
  'pending',
  'running',
  'completed',
  'failed',
]

async function fetchTasks(
  taskType: TaskTypeFilter,
  status: TaskStatusFilter,
): Promise<TaskRecord[]> {
  const query = {
    limit: MAX_TASKS,
    status: status === 'all' ? undefined : status,
    task_type: taskType === 'all' ? undefined : taskType,
  }
  const result = await getOvResult<unknown>(
    getTasks({
      query,
    }),
  )
  return normalizeTasks(result)
}

function TasksRoute() {
  const { i18n, t } = useTranslation('tasksPage')
  const { identityScopeKey } = useAppConnection()
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(DEFAULT_PAGE_SIZE)
  const [taskType, setTaskType] = React.useState<TaskTypeFilter>('all')
  const [statusFilter, setStatusFilter] =
    React.useState<TaskStatusFilter>('all')
  const [selectedTaskId, setSelectedTaskId] = React.useState<string | null>(
    null,
  )
  const tasksQuery = useQuery({
    queryFn: () => fetchTasks(taskType, statusFilter),
    queryKey: ['tasks', identityScopeKey, taskType, statusFilter],
    refetchInterval: 10_000,
  })
  const allTasks = tasksQuery.data ?? []
  const pageOffset = (page - 1) * pageSize
  const tasks = allTasks.slice(pageOffset, pageOffset + pageSize)
  const totalPages = Math.max(1, Math.ceil(allTasks.length / pageSize))
  const hasNext = page < totalPages
  const hasActiveFilters = taskType !== 'all' || statusFilter !== 'all'

  React.useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages)
    }
  }, [page, totalPages])

  const formatTime = (task: TaskRecord) => {
    const date = getTaskDate(task)
    if (!date) return '-'
    return new Intl.DateTimeFormat(i18n.resolvedLanguage, {
      dateStyle: 'medium',
      timeStyle: 'medium',
    }).format(date)
  }

  const renderStatus = (rawStatus: string | undefined) => {
    const status = normalizeTaskStatus(rawStatus)
    const Icon =
      status === 'completed'
        ? CheckCircle2Icon
        : status === 'failed'
          ? CircleXIcon
          : status === 'running'
            ? LoaderCircleIcon
            : CircleDashedIcon

    return (
      <Badge
        variant={
          status === 'failed'
            ? 'destructive'
            : status === 'completed'
              ? 'secondary'
              : 'outline'
        }
        className="gap-1 font-normal"
      >
        <Icon className={status === 'running' ? 'animate-spin' : undefined} />
        {t(`status.${status}`)}
      </Badge>
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('title')}
          </h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('description')}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={tasksQuery.isFetching}
          onClick={() => void tasksQuery.refetch()}
        >
          <RefreshCwIcon
            className={tasksQuery.isFetching ? 'animate-spin' : undefined}
          />
          {t('refresh')}
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card/60 p-2 shadow-xs">
        <span className="px-1 text-xs font-medium text-muted-foreground">
          {t('filters.label')}
        </span>
        <Select
          value={taskType}
          onValueChange={(value) => {
            setTaskType(value as TaskTypeFilter)
            setPage(1)
          }}
        >
          <SelectTrigger
            size="sm"
            className="min-w-40 bg-background"
            aria-label={t('filters.type')}
          >
            <SelectValue>
              {taskType === 'all'
                ? t('filters.allTypes')
                : t(`types.${taskType}`)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('filters.allTypes')}</SelectItem>
            {TASK_TYPE_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`types.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={statusFilter}
          onValueChange={(value) => {
            setStatusFilter(value as TaskStatusFilter)
            setPage(1)
          }}
        >
          <SelectTrigger
            size="sm"
            className="min-w-32 bg-background"
            aria-label={t('filters.status')}
          >
            <SelectValue>
              {statusFilter === 'all'
                ? t('filters.allStatuses')
                : t(`status.${statusFilter}`)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('filters.allStatuses')}</SelectItem>
            {TASK_STATUS_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`status.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {hasActiveFilters ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => {
              setTaskType('all')
              setStatusFilter('all')
              setPage(1)
            }}
          >
            {t('filters.clear')}
          </Button>
        ) : null}
      </div>

      {tasksQuery.isLoading ? (
        <Card className="min-h-56 items-center justify-center">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" />
            {t('loading')}
          </div>
        </Card>
      ) : tasksQuery.isError ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <CircleXIcon className="size-8 text-destructive/70" />
          <div className="grid gap-1">
            <p className="font-medium">{t('loadFailed')}</p>
            <p className="max-w-xl text-sm text-muted-foreground">
              {tasksQuery.error instanceof Error
                ? tasksQuery.error.message
                : String(tasksQuery.error)}
            </p>
          </div>
        </Card>
      ) : tasks.length === 0 ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <ClipboardListIcon className="size-5" />
          </div>
          <div className="grid max-w-md gap-1">
            <p className="font-medium">
              {t(hasActiveFilters ? 'emptyFiltered' : 'empty')}
            </p>
            <p className="text-sm text-muted-foreground">
              {t(
                hasActiveFilters
                  ? 'emptyFilteredDescription'
                  : 'emptyDescription',
              )}
            </p>
          </div>
        </Card>
      ) : (
        <Card className="gap-0 overflow-hidden py-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  <TableHead>{t('table.task')}</TableHead>
                  <TableHead>{t('table.type')}</TableHead>
                  <TableHead>{t('table.resource')}</TableHead>
                  <TableHead>{t('table.status')}</TableHead>
                  <TableHead className="text-right">
                    {t('table.createdAt')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((task, index) => {
                  const taskId = task.task_id
                  return (
                    <TableRow
                      key={taskId || String(index)}
                      tabIndex={taskId ? 0 : undefined}
                      aria-label={
                        taskId ? t('detail.openLabel', { taskId }) : undefined
                      }
                      className={cn(
                        taskId &&
                          'cursor-pointer outline-none hover:bg-muted/35 focus-visible:bg-muted/35 focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-inset',
                      )}
                      onClick={() => {
                        if (taskId) setSelectedTaskId(taskId)
                      }}
                      onKeyDown={(event) => {
                        if (
                          taskId &&
                          (event.key === 'Enter' || event.key === ' ')
                        ) {
                          event.preventDefault()
                          setSelectedTaskId(taskId)
                        }
                      }}
                    >
                      <TableCell>
                        <span className="flex items-center gap-2">
                          <code className="min-w-0 truncate text-xs">
                            {taskId || `#${pageOffset + index + 1}`}
                          </code>
                          {taskId ? (
                            <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                          ) : null}
                        </span>
                      </TableCell>
                      <TableCell className="font-medium">
                        {task.task_type || '-'}
                      </TableCell>
                      <TableCell className="max-w-72 truncate text-muted-foreground">
                        {task.resource_id || '-'}
                      </TableCell>
                      <TableCell>{renderStatus(task.status)}</TableCell>
                      <TableCell className="whitespace-nowrap text-right text-muted-foreground">
                        {formatTime(task)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
          <div className="flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center justify-center gap-3 sm:justify-start">
              <p className="text-sm text-muted-foreground">
                {t('pagination.page', { page })}
              </p>
              <Select
                value={String(pageSize)}
                onValueChange={(value) => {
                  setPageSize(Number(value))
                  setPage(1)
                }}
              >
                <SelectTrigger size="sm" aria-label={t('pagination.pageSize')}>
                  <SelectValue>
                    {t('pagination.pageSizeValue', { count: pageSize })}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZE_OPTIONS.map((option) => (
                    <SelectItem key={option} value={String(option)}>
                      {t('pagination.pageSizeValue', { count: option })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Pagination className="mx-0 w-auto justify-center sm:justify-end">
              <PaginationContent>
                <PaginationItem>
                  <PaginationPrevious
                    href="#"
                    text={t('pagination.previous')}
                    aria-disabled={page <= 1}
                    className={cn(
                      page <= 1 && 'pointer-events-none opacity-50',
                    )}
                    onClick={(event) => {
                      event.preventDefault()
                      if (page > 1) setPage((current) => current - 1)
                    }}
                  />
                </PaginationItem>
                <PaginationItem>
                  <PaginationNext
                    href="#"
                    text={t('pagination.next')}
                    aria-disabled={!hasNext}
                    className={cn(!hasNext && 'pointer-events-none opacity-50')}
                    onClick={(event) => {
                      event.preventDefault()
                      if (hasNext) setPage((current) => current + 1)
                    }}
                  />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          </div>
        </Card>
      )}

      <TaskDetailSheet
        identityScopeKey={identityScopeKey}
        open={Boolean(selectedTaskId)}
        taskId={selectedTaskId}
        onOpenChange={(open) => {
          if (!open) setSelectedTaskId(null)
        }}
      />
    </div>
  )
}
