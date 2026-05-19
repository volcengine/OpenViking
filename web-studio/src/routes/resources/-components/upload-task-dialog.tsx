import { ChevronDown, ChevronUp, FileIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { cn } from '#/lib/utils'
import type { ResourceUploadTask } from '../-hooks/use-resource-upload'
import { formatFileSize } from '../-lib/upload'

function TaskStatusBadge({ status }: { status: ResourceUploadTask['status'] }) {
  const { t } = useTranslation('resources')

  if (status === 'success') {
    return (
      <Badge variant="secondary" className="bg-emerald-500/12 text-emerald-400">
        {t('processingTasks.status.success')}
      </Badge>
    )
  }

  if (status === 'failed') {
    return (
      <Badge variant="secondary" className="bg-rose-500/12 text-black">
        {t('processingTasks.status.failed')}
      </Badge>
    )
  }

  return (
    <Badge variant="secondary" className="bg-amber-500/12 text-amber-300">
      {t('processingTasks.status.processing')}
    </Badge>
  )
}

type UploadTaskDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  tasks: ResourceUploadTask[]
}

export function UploadTaskDialog({
  open,
  onOpenChange,
  tasks,
}: UploadTaskDialogProps) {
  const { t } = useTranslation('resources')
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<string>>(new Set())

  const sortedTasks = useMemo(
    () => [...tasks].sort((a, b) => b.createdAt - a.createdAt),
    [tasks],
  )

  const toggleTask = (taskId: string) => {
    setExpandedTaskIds((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(80vh,640px)] gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="border-b px-6 py-5 pr-16">
          <DialogTitle className="truncate text-xl">
            {t('processingTasks.title')}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[calc(min(80vh,640px)-5.5rem)] overflow-y-auto px-6 py-5">
          {sortedTasks.length === 0 ? (
            <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
              {t('processingTasks.empty')}
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border/60 bg-muted/10">
              <div className="grid grid-cols-[minmax(0,1fr)_140px_96px] gap-4 border-b border-border/60 px-4 py-3 text-xs font-medium text-muted-foreground">
                <div>{t('processingTasks.columns.fileName')}</div>
                <div>{t('processingTasks.columns.status')}</div>
                <div>{t('processingTasks.columns.size')}</div>
              </div>

              <div>
                {sortedTasks.map((task) => {
                  const isFailed = task.status === 'failed'
                  const isExpanded = expandedTaskIds.has(task.id)
                  const hasDetail =
                    isFailed && (task.errorCode || task.errorMessage)

                  return (
                    <div
                      key={task.id}
                      className="border-b border-border/50 last:border-b-0"
                    >
                      <div
                        className={cn(
                          'grid grid-cols-[minmax(0,1fr)_140px_96px] gap-4 px-4 py-3 text-sm',
                          isFailed && 'bg-rose-500/6',
                        )}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                            <span className="truncate font-medium">
                              {task.fileName}
                            </span>
                          </div>
                        </div>

                        <div className="flex items-center gap-2">
                          <TaskStatusBadge status={task.status} />
                          {hasDetail ? (
                            <button
                              type="button"
                              className="inline-flex items-center text-muted-foreground transition-colors hover:text-foreground"
                              onClick={() => toggleTask(task.id)}
                              aria-label={t('processingTasks.toggleError')}
                            >
                              {isExpanded ? (
                                <ChevronUp className="size-4" />
                              ) : (
                                <ChevronDown className="size-4" />
                              )}
                            </button>
                          ) : null}
                        </div>

                        <div className="flex items-center text-muted-foreground">
                          {typeof task.fileSize === 'number'
                            ? formatFileSize(task.fileSize)
                            : '-'}
                        </div>
                      </div>

                      {hasDetail && isExpanded ? (
                        <div className="space-y-1 border-t border-border/40 bg-rose-500/6 px-10 py-3 text-sm">
                          {task.errorCode ? (
                            <div className="font-mono text-black">
                              {task.errorCode}
                            </div>
                          ) : null}
                          {task.errorMessage ? (
                            <div className="text-black">
                              {task.errorMessage}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
