import type { TFunction } from 'i18next'

export type TaskEvent = {
  seq?: number
  event_id?: string
  reason?: string
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
  discarded_count?: number
}

export type PendingTaskEvents = {
  items: TaskEvent[]
  dropped_count: number
}

export function mergeTaskEvents(
  history?: TaskEventHistory | null,
  pending?: PendingTaskEvents,
) {
  const events = new Map<string, TaskEvent>()
  for (const event of [...(history?.items ?? []), ...(pending?.items ?? [])]) {
    const key = taskEventKey(event)
    if (!events.has(key)) events.set(key, event)
  }
  return [...events.values()]
}

export function taskEventKey(event: TaskEvent) {
  return event.event_id ?? `legacy:${event.seq}`
}

export function describeTaskOperation(
  operation: string,
  t: TFunction<'tasksPage'>,
) {
  switch (operation) {
    case 'archive_summary':
      return t('events.archiveSummary')
    case 'long_term_memory_extraction':
      return t('events.longTermMemoryExtraction')
    default:
      return operation
  }
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
    case 'operation_started':
      return t('events.operationStarted')
    case 'operation_completed':
      return t('events.operationCompleted')
    case 'operation_failed':
      return t('events.operationFailed')
    case 'operation_cancelled':
      return t('events.operationCancelled')
    case 'operation_skipped':
      return t('events.operationSkipped')
    default:
      return event.kind
  }
}

export function formatTaskEvent(event: TaskEvent, t: TFunction<'tasksPage'>) {
  return [
    `[${event.recorded_at}]`,
    describeTaskEvent(event, t),
    event.stage ? `stage=${event.stage}` : '',
    event.operation ? describeTaskOperation(event.operation, t) : '',
    event.reason
      ? t(`events.skipReasons.${event.reason}`, { defaultValue: event.reason })
      : '',
    event.error ?? '',
  ]
    .filter(Boolean)
    .join(' ')
}
