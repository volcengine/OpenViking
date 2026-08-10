import type { WatchTask } from './api'
import { normalizeTaskStatus } from '#/routes/tasks/-lib/task-record'
import type { TaskRecord } from '#/routes/tasks/-lib/task-record'

export const WATCH_DISCOVERY_INTERVAL_MS = 1_000

export function getWatchRefetchInterval(
  isDiscovering: boolean,
): number | false {
  return isDiscovering ? WATCH_DISCOVERY_INTERVAL_MS : false
}

export function normalizeWatchUri(uri: string): string {
  return uri.replace(/\/+$/, '')
}

export function hasDiscoveredWatch(
  watches: WatchTask[],
  targetUri: string,
): boolean {
  const normalizedTarget = normalizeWatchUri(targetUri)
  return watches.some(
    (watch) => normalizeWatchUri(watch.toUri) === normalizedTarget,
  )
}

export function hasCompletedWatchSync(
  watches: WatchTask[],
  taskId: string,
  previousExecutionTime: string | null,
): boolean {
  const watch = watches.find((item) => item.taskId === taskId)
  return Boolean(watch && watch.lastExecutionTime !== previousExecutionTime)
}

export function hasActiveWatchProcessing(tasks: TaskRecord[]): boolean {
  return tasks.some((task) => {
    const status = normalizeTaskStatus(task.status)
    return (
      status === 'pending' || status === 'running' || status === 'cancelling'
    )
  })
}
