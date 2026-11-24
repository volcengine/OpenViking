import type { TFunction } from 'i18next'

export type TaskEvent = {
  seq: number
  recorded_at: string
  kind: string
  status: string
  stage: string | null
  operation: string | null
  error: string | null
}

export type TaskEventHistory = {
  items: TaskEvent[]
  dropped_count: number
  started_mid_task: boolean
}

export function describeTaskEvent(event: TaskEvent, t: TFunction<'tasksPage'>) {
  switch (event.kind) {
    case 'created':
      return t('events.created')
    case 'status_changed':
      return t('events.statusChanged', {
        status: t(`status.${event.status}`, { defaultValue: event.status }),
      })
    case 'stage_changed':
      return t('events.stageChanged', { stage: event.stage ?? '-' })
    case 'error_recorded':
      return t('events.errorRecorded')
    case 'waiting_for_descendants':
      return t('events.waitingForDescendants')
    default:
      return event.kind
  }
}

export function formatTaskEvent(event: TaskEvent, t: TFunction<'tasksPage'>) {
  return [
    `#${event.seq} [${event.recorded_at}]`,
    describeTaskEvent(event, t),
    event.stage ? `stage=${event.stage}` : '',
    event.operation ? `operation=${event.operation}` : '',
    event.error ?? '',
  ]
    .filter(Boolean)
    .join(' ')
}
