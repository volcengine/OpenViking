import { CopyIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'
import { describeTaskEvent, formatTaskEvent } from '../-lib/task-events'
import type { TaskRecord } from '../-lib/task-record'

export function TaskExecutionEvents({ task }: { task: TaskRecord }) {
  const { t, i18n } = useTranslation('tasksPage')
  const history = task.execution_events
  const notices = [
    history?.started_mid_task ? t('events.partial') : '',
    history?.dropped_count
      ? t('events.truncated', { count: history.dropped_count })
      : '',
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
          ...(history?.items.map((event) => formatTaskEvent(event, t)) ?? []),
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
          disabled={!history?.items.length}
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
      {!history?.items.length ? (
        <p className="text-sm text-muted-foreground">
          {history === undefined ? t('events.unsupported') : t('events.empty')}
        </p>
      ) : (
        <ol className="max-h-64 space-y-3 overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs">
          {history.items.map((event) => (
            <li
              key={event.seq}
              className={cn(
                'grid gap-1',
                (event.error !== null || event.status === 'failed') &&
                  'text-destructive',
              )}
            >
              <div className="text-muted-foreground">
                <span>#{event.seq} </span>
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
                <p>{t('events.operation', { operation: event.operation })}</p>
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
