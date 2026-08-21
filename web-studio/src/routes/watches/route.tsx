import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import {
  Clock3Icon,
  EllipsisIcon,
  HistoryIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'
import { Button } from '#/components/ui/button'
import { Card } from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { Switch } from '#/components/ui/switch'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  AddResourceForm,
  ResourceUploadProvider,
} from '#/routes/resources/resource-upload'

import { EditWatchDialog } from './-components/edit-watch-dialog'
import { WatchHistorySheet } from './-components/watch-history-sheet'
import {
  deleteWatch,
  fetchWatchProcessingHistory,
  fetchWatches,
  triggerWatch,
  updateWatch,
} from './-lib/api'
import type { UpdateWatchInput, WatchTask } from './-lib/api'
import {
  getWatchRefetchInterval,
  hasCompletedTriggeredWatchSync,
  hasDiscoveredWatch,
  hasWatchExecutionAdvanced,
  normalizeWatchUri,
} from './-lib/watch-discovery'

const WATCH_DISCOVERY_TIMEOUT_MS = 60_000

type PendingWatch = {
  expiresAt: number
  identityScopeKey: string
  toUri: string
}

type PendingWatchSync = {
  baselineExecutionTime: string | null
  baselineTaskIds: string[] | null
  identityScopeKey: string
  syncId: number
  taskId: string
  toUri: string
}

export const Route = createFileRoute('/watches')({
  component: WatchesRoute,
})

function WatchesRoute() {
  return (
    <ResourceUploadProvider>
      <WatchManagementPage />
    </ResourceUploadProvider>
  )
}

export function WatchManagementPage() {
  const { i18n, t } = useTranslation('watchesPage')
  const { identityScopeKey } = useAppConnection()
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = React.useState(false)
  const [isCreatingWatch, setIsCreatingWatch] = React.useState(false)
  const watchCreationPendingRef = React.useRef(false)
  const watchCreationTargetRef = React.useRef<string | null>(null)
  const watchCreationToastRef = React.useRef<string | number | null>(null)
  const [pendingWatch, setPendingWatch] = React.useState<PendingWatch | null>(
    null,
  )
  const [pendingSync, setPendingSync] = React.useState<PendingWatchSync | null>(
    null,
  )
  const [editingWatch, setEditingWatch] = React.useState<WatchTask | null>(null)
  const [deletingWatch, setDeletingWatch] = React.useState<WatchTask | null>(
    null,
  )
  const [historyWatch, setHistoryWatch] = React.useState<WatchTask | null>(null)
  const watchesQuery = useQuery({
    queryFn: fetchWatches,
    queryKey: ['watches', identityScopeKey],
    refetchInterval: getWatchRefetchInterval(
      pendingWatch?.identityScopeKey === identityScopeKey ||
        pendingSync?.identityScopeKey === identityScopeKey,
    ),
  })
  const watches = watchesQuery.data ?? []
  const syncTasksQuery = useQuery({
    enabled:
      pendingSync?.identityScopeKey === identityScopeKey &&
      pendingSync.baselineTaskIds !== null,
    queryFn: () => fetchWatchProcessingHistory(pendingSync?.toUri ?? ''),
    queryKey: [
      'watch-sync-progress',
      identityScopeKey,
      pendingSync?.taskId,
      pendingSync?.syncId,
    ],
    refetchInterval:
      pendingSync?.identityScopeKey === identityScopeKey &&
      pendingSync.baselineTaskIds !== null
        ? 1_000
        : false,
  })

  const refreshWatches = React.useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: ['watches', identityScopeKey],
    })
  }, [identityScopeKey, queryClient])

  const beginWatchCreation = React.useCallback(() => {
    watchCreationPendingRef.current = true
    watchCreationTargetRef.current = null
    setIsCreatingWatch(true)
    watchCreationToastRef.current = toast.loading(t('toast.creating'))
  }, [t])

  const finishWatchCreation = React.useCallback(() => {
    if (!watchCreationPendingRef.current) return
    watchCreationPendingRef.current = false
    watchCreationTargetRef.current = null
    setIsCreatingWatch(false)
    toast.success(t('toast.created'), {
      id: watchCreationToastRef.current ?? undefined,
    })
    watchCreationToastRef.current = null
  }, [t])

  const failWatchCreation = React.useCallback(() => {
    watchCreationPendingRef.current = false
    watchCreationTargetRef.current = null
    setIsCreatingWatch(false)
    if (watchCreationToastRef.current !== null) {
      toast.dismiss(watchCreationToastRef.current)
      watchCreationToastRef.current = null
    }
  }, [])

  const discoverWatch = React.useCallback(
    ({ rootUri }: { rootUri: string | null }) => {
      void refreshWatches()
      if (!rootUri) return
      watchCreationTargetRef.current = normalizeWatchUri(rootUri)
      setPendingWatch({
        expiresAt: Date.now() + WATCH_DISCOVERY_TIMEOUT_MS,
        identityScopeKey,
        toUri: normalizeWatchUri(rootUri),
      })
    },
    [identityScopeKey, refreshWatches],
  )

  React.useEffect(() => {
    if (
      pendingWatch?.identityScopeKey === identityScopeKey &&
      hasDiscoveredWatch(watches, pendingWatch.toUri)
    ) {
      finishWatchCreation()
      setPendingWatch(null)
    }
  }, [finishWatchCreation, identityScopeKey, pendingWatch, watches])

  React.useEffect(() => {
    if (!pendingWatch) return undefined
    const expiresAt = pendingWatch.expiresAt
    const timeout = window.setTimeout(
      () => {
        watchCreationPendingRef.current = false
        watchCreationTargetRef.current = null
        setIsCreatingWatch(false)
        toast.warning(t('toast.createTimeout'), {
          id: watchCreationToastRef.current ?? undefined,
        })
        watchCreationToastRef.current = null
        setPendingWatch((current) =>
          current?.expiresAt === expiresAt ? null : current,
        )
      },
      Math.max(0, pendingWatch.expiresAt - Date.now()),
    )
    return () => window.clearTimeout(timeout)
  }, [pendingWatch, t])

  React.useEffect(() => {
    if (pendingSync?.identityScopeKey !== identityScopeKey) return

    const historyShowsCompletion =
      syncTasksQuery.isFetched &&
      !syncTasksQuery.isFetching &&
      !syncTasksQuery.isError &&
      pendingSync.baselineTaskIds !== null &&
      hasCompletedTriggeredWatchSync(
        syncTasksQuery.data ?? [],
        pendingSync.baselineTaskIds,
      )

    const currentWatch = watches.find(
      (watch) => watch.taskId === pendingSync.taskId,
    )
    const watchShowsCompletion =
      pendingSync.baselineTaskIds === null &&
      hasWatchExecutionAdvanced(
        currentWatch?.lastExecutionTime ?? null,
        pendingSync.baselineExecutionTime,
      )

    if (historyShowsCompletion || watchShowsCompletion) {
      setPendingSync(null)
    }
  }, [identityScopeKey, pendingSync, syncTasksQuery, watches])

  const updateMutation = useMutation({
    mutationFn: ({
      input,
      taskId,
    }: {
      input: UpdateWatchInput
      taskId: string
    }) => updateWatch(taskId, input),
    onSuccess: async () => {
      await refreshWatches()
      setEditingWatch(null)
      toast.success(t('toast.updated'))
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })
  const triggerMutation = useMutation({
    mutationFn: ({
      taskId,
    }: {
      baselineExecutionTime: string | null
      taskId: string
      toUri: string
    }) => triggerWatch(taskId),
    onMutate: async ({ baselineExecutionTime, taskId, toUri }) => {
      const syncId = Date.now()
      setPendingSync({
        baselineExecutionTime,
        baselineTaskIds: null,
        identityScopeKey,
        syncId,
        taskId,
        toUri,
      })
      try {
        const baselineTasks = await fetchWatchProcessingHistory(toUri)
        setPendingSync((current) =>
          current?.syncId === syncId
            ? {
                ...current,
                baselineTaskIds: baselineTasks.flatMap((task) =>
                  task.task_id ? [task.task_id] : [],
                ),
              }
            : current,
        )
      } catch {
        // Fall back to the Watch execution time while the trigger proceeds.
      }
    },
    onSuccess: async () => {
      await Promise.all([
        refreshWatches(),
        queryClient.invalidateQueries({
          queryKey: ['watch-sync-progress', identityScopeKey],
        }),
      ])
      toast.success(t('toast.triggered'))
    },
    onError: (error) => {
      setPendingSync(null)
      toast.error(getErrorMessage(error))
    },
  })
  const deleteMutation = useMutation({
    mutationFn: deleteWatch,
    onSuccess: async () => {
      await refreshWatches()
      setDeletingWatch(null)
      toast.success(t('toast.deleted'))
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const formatTime = (value: string | null) => {
    if (!value) return t('never')
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return new Intl.DateTimeFormat(i18n.resolvedLanguage, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date)
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
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={watchesQuery.isFetching}
            onClick={() => void watchesQuery.refetch()}
          >
            <RefreshCwIcon
              className={watchesQuery.isFetching ? 'animate-spin' : undefined}
            />
            {t('refresh')}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={isCreatingWatch}
            onClick={() => setAddOpen(true)}
          >
            <PlusIcon />
            {t(isCreatingWatch ? 'adding' : 'add')}
          </Button>
        </div>
      </header>

      {isCreatingWatch ? (
        <div
          role="status"
          aria-live="polite"
          className="flex items-start gap-3 rounded-xl border border-sky-500/25 bg-sky-500/10 px-4 py-3 text-sky-800 dark:text-sky-300"
        >
          <RefreshCwIcon className="mt-0.5 size-4 shrink-0 animate-spin" />
          <div className="grid gap-0.5">
            <p className="text-sm font-medium">{t('creation.title')}</p>
            <p className="text-xs leading-5 text-sky-700/80 dark:text-sky-300/75">
              {t('creation.description')}
            </p>
          </div>
        </div>
      ) : null}

      <Card className="overflow-hidden py-0">
        {watchesQuery.isLoading ? (
          <PageState icon={<RefreshCwIcon className="animate-spin" />}>
            {t('loading')}
          </PageState>
        ) : watchesQuery.isError ? (
          <PageState icon={<Clock3Icon />}>
            <span>{t('loadFailed')}</span>
            <span className="max-w-xl text-xs text-muted-foreground">
              {getErrorMessage(watchesQuery.error)}
            </span>
          </PageState>
        ) : watches.length === 0 ? (
          <PageState icon={<Clock3Icon />}>
            <span>{t('empty')}</span>
            <span className="text-xs text-muted-foreground">
              {t('emptyDescription')}
            </span>
          </PageState>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-56">
                  {t('columns.resource')}
                </TableHead>
                <TableHead className="min-w-52">
                  {t('columns.source')}
                </TableHead>
                <TableHead>{t('columns.status')}</TableHead>
                <TableHead>{t('columns.interval')}</TableHead>
                <TableHead className="min-w-40">
                  {t('columns.lastRun')}
                </TableHead>
                <TableHead className="min-w-40">
                  {t('columns.nextRun')}
                </TableHead>
                <TableHead className="min-w-40 text-right">
                  {t('columns.actions')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {watches.map((watch) => {
                const isSyncing =
                  pendingSync?.identityScopeKey === identityScopeKey &&
                  pendingSync.taskId === watch.taskId

                return (
                  <TableRow key={watch.taskId}>
                    <TableCell>
                      <div
                        className="max-w-72 truncate font-mono text-xs"
                        title={watch.toUri}
                      >
                        {watch.toUri}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div
                        className="max-w-64 truncate text-xs"
                        title={watch.path}
                      >
                        {watch.path}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Switch
                          size="sm"
                          checked={watch.isActive}
                          disabled={updateMutation.isPending}
                          aria-label={t(
                            watch.isActive
                              ? 'actions.disable'
                              : 'actions.enable',
                          )}
                          onCheckedChange={(checked) =>
                            updateMutation.mutate({
                              input: { isActive: checked },
                              taskId: watch.taskId,
                            })
                          }
                        />
                        <span className="text-xs text-muted-foreground">
                          {t(
                            watch.isActive
                              ? 'status.active'
                              : 'status.disabled',
                          )}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      {formatInterval(watch.watchInterval, t)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatTime(watch.lastExecutionTime)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatTime(watch.nextExecutionTime)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <div className="flex flex-wrap justify-end gap-1">
                        <ActionButton
                          label={t(
                            isSyncing ? 'actions.syncing' : 'actions.trigger',
                          )}
                          disabled={
                            !watch.isActive ||
                            triggerMutation.isPending ||
                            pendingSync !== null
                          }
                          onClick={() =>
                            triggerMutation.mutate({
                              baselineExecutionTime: watch.lastExecutionTime,
                              taskId: watch.taskId,
                              toUri: watch.toUri,
                            })
                          }
                        >
                          <RotateCwIcon
                            className={isSyncing ? 'animate-spin' : undefined}
                          />
                        </ActionButton>
                        <ActionButton
                          label={t('actions.edit')}
                          disabled={updateMutation.isPending}
                          onClick={() => setEditingWatch(watch)}
                        >
                          <PencilIcon />
                        </ActionButton>
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                className="h-8 px-2 text-xs"
                                aria-label={t('actions.more')}
                                title={t('actions.more')}
                              />
                            }
                          >
                            <EllipsisIcon />
                            <span>{t('actions.more')}</span>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onClick={() => setHistoryWatch(watch)}
                            >
                              <HistoryIcon />
                              {t('actions.history')}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              variant="destructive"
                              disabled={deleteMutation.isPending}
                              onClick={() => setDeletingWatch(watch)}
                            >
                              <Trash2Icon />
                              {t('actions.delete')}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-h-[min(86vh,760px)] gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="text-xl">
              {t('addDialog.title')}
            </DialogTitle>
            <DialogDescription>{t('addDialog.description')}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[calc(min(86vh,760px)-6rem)] overflow-y-auto px-6 py-5">
            <AddResourceForm
              initialMode="remote"
              initialWatchEnabled
              watchRequired
              onAccepted={discoverWatch}
              onCompleted={() => {
                void refreshWatches()
                if (watchCreationTargetRef.current === null) {
                  finishWatchCreation()
                }
              }}
              onFailed={failWatchCreation}
              onSubmitted={() => {
                beginWatchCreation()
                setAddOpen(false)
              }}
            />
          </div>
        </DialogContent>
      </Dialog>

      <WatchHistorySheet
        identityScopeKey={identityScopeKey}
        open={historyWatch !== null}
        watch={historyWatch}
        onOpenChange={(open) => {
          if (!open) setHistoryWatch(null)
        }}
      />

      <EditWatchDialog
        watch={editingWatch}
        pending={updateMutation.isPending}
        onOpenChange={(open) => !open && setEditingWatch(null)}
        onSubmit={(input) => {
          if (!editingWatch) return
          updateMutation.mutate({
            input,
            taskId: editingWatch.taskId,
          })
        }}
      />

      <AlertDialog
        open={Boolean(deletingWatch)}
        onOpenChange={(open) => !open && setDeletingWatch(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteDialog.title')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('deleteDialog.description', {
                uri: deletingWatch?.toUri ?? '',
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              {t('cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteMutation.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (deletingWatch) {
                  deleteMutation.mutate(deletingWatch.taskId)
                }
              }}
            >
              {t('actions.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ActionButton({
  children,
  disabled,
  label,
  onClick,
}: {
  children: React.ReactNode
  disabled?: boolean
  label: string
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="h-8 px-2 text-xs"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
      <span>{label}</span>
    </Button>
  )
}

function PageState({
  children,
  icon,
}: {
  children: React.ReactNode
  icon: React.ReactNode
}) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center gap-2 px-6 text-center text-sm [&_svg]:size-7 [&_svg]:text-muted-foreground">
      {icon}
      {children}
    </div>
  )
}

function formatInterval(
  minutes: number,
  t: ReturnType<typeof useTranslation<'watchesPage'>>['t'],
): string {
  if (minutes % 1440 === 0) return t('interval.days', { count: minutes / 1440 })
  if (minutes % 60 === 0) return t('interval.hours', { count: minutes / 60 })
  return t('interval.minutes', { count: minutes })
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
