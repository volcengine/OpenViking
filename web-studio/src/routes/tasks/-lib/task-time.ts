import { isActiveTaskStatus } from './task-record'

export type TaskTimestamp = {
  processing_seconds?: number | null
  created_at?: number | string
  created_at_iso?: string
  status?: string
  updated_at?: number | string
  updated_at_iso?: string
}

export function getTaskDate(task: TaskTimestamp): Date | undefined {
  const value =
    task.created_at_iso ??
    task.updated_at_iso ??
    task.created_at ??
    task.updated_at
  if (value === undefined) return undefined

  const numericValue =
    typeof value === 'number'
      ? value
      : value.trim() !== '' && Number.isFinite(Number(value))
        ? Number(value)
        : undefined
  const normalizedValue =
    numericValue === undefined
      ? value
      : Math.abs(numericValue) < 1_000_000_000_000
        ? numericValue * 1_000
        : numericValue
  const date = new Date(normalizedValue)
  return Number.isNaN(date.getTime()) ? undefined : date
}

const terminalStatuses = new Set(['completed', 'failed', 'cancelled'])

/** Wall-clock time since submission, including queue waits between stages. */
export function getTaskDurationSeconds(
  task: TaskTimestamp,
  nowMs = Date.now(),
): number | undefined {
  // A missing creation timestamp cannot be replaced with the last update.
  const start = getTaskDate({
    created_at: task.created_at,
    created_at_iso: task.created_at_iso,
  })
  if (!start) return undefined

  let endMs: number | undefined
  if (isActiveTaskStatus(task.status)) {
    endMs = nowMs
  } else if (terminalStatuses.has(task.status ?? '')) {
    endMs = getTaskDate({
      created_at: task.updated_at,
      created_at_iso: task.updated_at_iso,
    })?.getTime()
  }
  if (endMs === undefined || !Number.isFinite(endMs)) return undefined
  if (endMs < start.getTime()) return undefined
  return (endMs - start.getTime()) / 1000
}

export function getAverageTaskDurationSeconds(
  tasks: TaskTimestamp[],
): number | undefined {
  const durations = tasks
    .filter((task) => terminalStatuses.has(task.status ?? ''))
    .map((task) => getTaskDurationSeconds(task))
    .filter((duration): duration is number => duration !== undefined)
  if (durations.length === 0) return undefined
  return (
    durations.reduce((sum, duration) => sum + duration, 0) / durations.length
  )
}

export function formatTaskDuration(task: TaskTimestamp): string {
  const seconds = getTaskDurationSeconds(task)
  return seconds === undefined ? '-' : formatDurationString(Math.floor(seconds))
}

export function getTaskProcessingSeconds(
  task: TaskTimestamp,
): number | undefined {
  const seconds = task.processing_seconds
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
    ? seconds
    : undefined
}

export function formatTaskProcessingDuration(
  task: TaskTimestamp,
): string | undefined {
  const seconds = getTaskProcessingSeconds(task)
  return seconds === undefined
    ? undefined
    : formatDurationString(Math.floor(seconds))
}

export function formatTaskWaitingDuration(
  task: TaskTimestamp,
): string | undefined {
  const processing = getTaskProcessingSeconds(task)
  const total = getTaskDurationSeconds(task)
  if (processing === undefined || total === undefined) return undefined
  return formatDurationString(Math.floor(Math.max(0, total - processing)))
}

function formatDurationString(diffSec: number): string {
  if (diffSec < 1) return '< 1s'
  if (diffSec < 60) return `${diffSec}s`

  const mins = Math.floor(diffSec / 60)
  const secs = diffSec % 60

  if (mins < 60) {
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
  }

  const hours = Math.floor(mins / 60)
  const remMins = mins % 60
  return remMins > 0 ? `${hours}h ${remMins}m` : `${hours}h`
}
