import { CopyIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'
import {
  describeTaskEvent,
  describeTaskOperation,
  formatTaskEvent,
  mergeTaskEvents,
  taskEventKey,
} from '../-lib/task-events'
import type { TaskRecord } from '../-lib/task-record'

export function TaskExecutionEvents({
  task,
  refreshFailed = false,
}: {
  task: TaskRecord
  refreshFailed?: boolean
}) {
  const { t, i18n } = useTranslation('tasksPage')
  const history = task.execution_events
  const events = mergeTaskEvents(history, task.pending_execution_events)
  const discarded =
    (history?.discarded_count ?? 0) +
    (task.pending_execution_events?.dropped_count ?? 0)
  const notices = [
    history?.started_mid_task ? t('events.partial') : '',
    history?.dropped_count
      ? t('events.truncated', { count: history.dropped_count })
      : '',
    discarded ? t('events.discarded', { count: discarded }) : '',
  ].filter(Boolean)

  async function copyEvents() {
    const context = {
      task_id: task.task_id,
      task_type: task.task_type,
      resource_id: task.resource_id,
    }
    try {
      await navigator.clipboard.writeText(
        [
          `${t('events.context')}: ${JSON.stringify(context)}`,
          ...notices,
          ...(refreshFailed ? [t('events.refreshFailed')] : []),
          ...events.map((event) => formatTaskEvent(event, t)),
        ].join('\n'),
      )
      toast.success(t('events.copied'))
    } catch {
      toast.error(t('events.copyFailed'))
    }
  }

  return (
    <section className="grid gap-3" aria-label={t('events.title')}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{t('events.title')}</h3>
        <Button
          variant="ghost"
          size="sm"
          disabled={!events.length}
          onClick={() => void copyEvents()}
        >
          <CopyIcon />
          {t('events.copy')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t('events.description')}</p>
      {notices.map((notice) => (
        <p key={notice} className="text-xs text-muted-foreground">
          {notice}
        </p>
      ))}
      {!events.length ? (
        <p className="text-sm text-muted-foreground">
          {history === undefined ? t('events.unsupported') : t('events.empty')}
        </p>
      ) : (
        <ol className="max-h-64 space-y-3 overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs">
          {events.map((event) => (
            <li
              key={taskEventKey(event)}
              className={cn(
                'grid gap-1',
                (event.error !== null || event.status === 'failed') &&
                  'text-destructive',
              )}
            >
              <div className="text-muted-foreground">
                <time dateTime={event.recorded_at} title={event.recorded_at}>
                  {new Date(event.recorded_at).toLocaleString(
                    i18n.resolvedLanguage,
                    {
                      year: 'numeric',
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                      fractionalSecondDigits: 3,
                    },
                  )}
                </time>
              </div>
              <p>{describeTaskEvent(event, t)}</p>
              {event.stage && (
                <p>{t('events.stageContext', { stage: event.stage })}</p>
              )}
              {event.operation && (
                <p>
                  {t('events.operation', {
                    operation: describeTaskOperation(event.operation, t),
                  })}
                </p>
              )}
              {event.reason && (
                <p>
                  {t(`events.skipReasons.${event.reason}`, {
                    defaultValue: event.reason,
                  })}
                </p>
              )}
              {event.error !== null && (
                <p className="whitespace-pre-wrap break-words">{event.error}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
