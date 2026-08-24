import { getOvResult, getTasks } from '#/lib/ov-client'
import {
  normalizeTasks,
  normalizeTaskStatus,
} from '#/routes/tasks/-lib/task-record'
import type { TaskRecord, TaskStatus } from '#/routes/tasks/-lib/task-record'

export type TaskStatusFilter = Exclude<TaskStatus, 'unknown'> | 'all'

export type TaskTypeFilter =
  | 'add_resource'
  | 'add_skill'
  | 'admin_reindex'
  | 'connector_import'
  | 'legacy_cleanup'
  | 'legacy_migration'
  | 'session_commit'
  | 'snapshot_restore_reindex'
  | 'all'

export const MAX_TASKS = 200
const MAX_EFFECTIVE_RUNNING_TASKS = 8

export function getEffectiveTaskStatus(
  taskItem: TaskRecord,
  list: TaskRecord[],
): TaskStatus {
  const rawStatus = normalizeTaskStatus(taskItem.status)
  if (rawStatus !== 'running') {
    return rawStatus
  }
  const runningList = list
    .filter((task) => normalizeTaskStatus(task.status) === 'running')
    .sort(
      (left, right) =>
        Number(left.created_at || 0) - Number(right.created_at || 0),
    )
  const index = runningList.findIndex(
    (task) => task.task_id === taskItem.task_id,
  )
  return index >= MAX_EFFECTIVE_RUNNING_TASKS ? 'pending' : 'running'
}

export async function fetchTasks(
  taskType: TaskTypeFilter,
  status: TaskStatusFilter,
): Promise<TaskRecord[]> {
  const result = await getOvResult<unknown>(
    getTasks({
      query: {
        limit: MAX_TASKS,
        ...(taskType === 'all' ? {} : { task_type: taskType }),
      },
    }),
  )
  const fetched = normalizeTasks(result).sort(
    (left, right) =>
      Number(right.created_at || 0) - Number(left.created_at || 0),
  )
  if (status !== 'all') {
    return fetched.filter(
      (task) => getEffectiveTaskStatus(task, fetched) === status,
    )
  }
  return fetched
}
