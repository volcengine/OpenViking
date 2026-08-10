import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { Clock3Icon, PlusIcon, RefreshCwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { useAppConnection } from '#/hooks/use-app-connection'
import { AddResourceForm } from '#/routes/resources/-components/add-resource-page'
import { ResourceUploadProvider } from '#/routes/resources/-hooks/use-resource-upload'

import { fetchWatches } from './-lib/api'

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

function WatchManagementPage() {
  const { i18n, t } = useTranslation('watchesPage')
  const { identityScopeKey } = useAppConnection()
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = React.useState(false)
  const watchesQuery = useQuery({
    queryFn: fetchWatches,
    queryKey: ['watches', identityScopeKey],
    refetchInterval: 30_000,
  })
  const watches = watchesQuery.data ?? []

  const refreshWatches = React.useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: ['watches', identityScopeKey],
    })
  }, [identityScopeKey, queryClient])

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
          <Button type="button" size="sm" onClick={() => setAddOpen(true)}>
            <PlusIcon />
            {t('add')}
          </Button>
        </div>
      </header>

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
              </TableRow>
            </TableHeader>
            <TableBody>
              {watches.map((watch) => (
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
                    <Badge variant={watch.isActive ? 'secondary' : 'outline'}>
                      {t(watch.isActive ? 'status.active' : 'status.paused')}
                    </Badge>
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
                </TableRow>
              ))}
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
              onCompleted={() => void refreshWatches()}
              onSubmitted={() => {
                setAddOpen(false)
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
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
