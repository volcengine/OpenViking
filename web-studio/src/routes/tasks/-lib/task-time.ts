export type TaskTimestamp = {
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

export function formatTaskDuration(
  task: TaskTimestamp,
  _isZh: boolean = true,
): string {
  const status = task.status || 'unknown'

  // Pending tasks have not started execution yet
  if (status === 'pending') {
    return '-'
  }

  const createdDate = getTaskDate(task)
  if (!createdDate) return '-'

  const createdMs = createdDate.getTime()
  const updatedDate =
    task.updated_at || task.updated_at_iso
      ? getTaskDate({
          created_at: task.updated_at,
          created_at_iso: task.updated_at_iso,
        })
      : undefined

  const startMs = updatedDate ? updatedDate.getTime() : createdMs

  if (status === 'running') {
    const elapsedSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000))
    return formatDurationString(elapsedSec)
  }

  // Completed or Failed tasks
  const endMs = updatedDate ? updatedDate.getTime() : createdMs
  const durationSec = Math.max(0, Math.floor((endMs - createdMs) / 1000))
  return formatDurationString(durationSec)
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
