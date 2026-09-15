import { getOvResult, getTasks } from '#/lib/ov-client'
import { normalizeTasks } from '#/routes/tasks/-lib/task-record'
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

export async function fetchTasks(
  taskType: TaskTypeFilter,
  status: TaskStatusFilter,
): Promise<TaskRecord[]> {
  const result = await getOvResult<unknown>(
    getTasks({
      query: {
        limit: MAX_TASKS,
        ...(taskType === 'all' ? {} : { task_type: taskType }),
        ...(status === 'all' ? {} : { status }),
      },
    }),
  )
  return normalizeTasks(result).sort(
    (left, right) =>
      Number(right.created_at || 0) - Number(left.created_at || 0),
  )
}
