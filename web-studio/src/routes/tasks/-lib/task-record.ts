import type { TaskTimestamp } from './task-time'

export type TaskStatus =
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'unknown'

export type TaskRecord = TaskTimestamp & {
  error?: string | null
  resource_id?: string | null
  result?: unknown
  stage?: string | null
  status?: string
  task_id?: string
  task_type?: string
  updated_at?: number | string
  updated_at_iso?: string
}

export function normalizeTaskRecord(value: unknown): TaskRecord | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }
  return value as TaskRecord
}

export function normalizeTasks(value: unknown): TaskRecord[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map(normalizeTaskRecord)
    .filter((item): item is TaskRecord => Boolean(item))
}

export function normalizeTaskStatus(status: string | undefined): TaskStatus {
  if (
    status === 'completed' ||
    status === 'failed' ||
    status === 'pending' ||
    status === 'running'
  ) {
    return status
  }
  return 'unknown'
}

export function hasTaskResult(result: unknown): boolean {
  if (result === null || result === undefined) {
    return false
  }
  if (Array.isArray(result)) {
    return result.length > 0
  }
  if (typeof result === 'object') {
    return Object.keys(result).length > 0
  }
  return true
}
