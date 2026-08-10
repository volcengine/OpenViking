import { getOvResult, ovClient } from '#/lib/ov-client'

export type WatchTask = {
  createdAt: string | null
  instruction: string
  isActive: boolean
  lastExecutionTime: string | null
  nextExecutionTime: string | null
  path: string
  reason: string
  taskId: string
  toUri: string
  watchInterval: number
}

type WatchListResult = {
  tasks?: unknown[]
  total?: number
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function optionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

export function normalizeWatchTask(value: unknown): WatchTask | null {
  const task = asRecord(value)
  const taskId = optionalString(task?.task_id)
  const path = optionalString(task?.path)
  const toUri = optionalString(task?.to_uri)
  const watchInterval = task?.watch_interval

  if (
    !taskId ||
    !path ||
    !toUri ||
    typeof watchInterval !== 'number' ||
    !Number.isFinite(watchInterval)
  ) {
    return null
  }

  return {
    createdAt: optionalString(task?.created_at),
    instruction: optionalString(task?.instruction) ?? '',
    isActive: task?.is_active === true,
    lastExecutionTime: optionalString(task?.last_execution_time),
    nextExecutionTime: optionalString(task?.next_execution_time),
    path,
    reason: optionalString(task?.reason) ?? '',
    taskId,
    toUri,
    watchInterval,
  }
}

export function normalizeWatchList(value: unknown): WatchTask[] {
  const result = asRecord(value) as WatchListResult | null
  const tasks = Array.isArray(result?.tasks) ? result.tasks : []
  return tasks.flatMap((task) => {
    const normalized = normalizeWatchTask(task)
    return normalized ? [normalized] : []
  })
}

export async function fetchWatches(): Promise<WatchTask[]> {
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      url: '/api/v1/watches',
    }),
  )
  return normalizeWatchList(result)
}
