import { getOvResult, getTasks, ovClient } from '#/lib/ov-client'
import { normalizeTasks } from '#/routes/tasks/task-model'
import type { TaskRecord } from '#/routes/tasks/task-model'

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

export type UpdateWatchInput = {
  instruction?: string
  isActive?: boolean
  reason?: string
  watchInterval?: number
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

export async function fetchWatchProcessingHistory(
  toUri: string,
): Promise<TaskRecord[]> {
  const result = await getOvResult<unknown>(
    getTasks({
      query: {
        limit: 200,
        resource_id: toUri,
        task_type: 'add_resource',
      },
    }),
  )
  return normalizeTasks(result)
}

export async function updateWatch(
  taskId: string,
  input: UpdateWatchInput,
): Promise<void> {
  const body: Record<string, unknown> = {}
  if (input.watchInterval !== undefined) {
    body.watch_interval = input.watchInterval
  }
  if (input.isActive !== undefined) body.is_active = input.isActive
  if (input.reason !== undefined) body.reason = input.reason
  if (input.instruction !== undefined) body.instruction = input.instruction

  await getOvResult(
    ovClient.client.patch({
      body,
      url: `/api/v1/watches/${encodeURIComponent(taskId)}`,
    }),
  )
}

export async function triggerWatch(taskId: string): Promise<void> {
  await getOvResult(
    ovClient.client.post({
      url: `/api/v1/watches/${encodeURIComponent(taskId)}/trigger`,
    }),
  )
}

export async function deleteWatch(taskId: string): Promise<void> {
  await getOvResult(
    ovClient.client.delete({
      url: `/api/v1/watches/${encodeURIComponent(taskId)}`,
    }),
  )
}
