import { getOvResult, getTasks } from '#/lib/ov-client'
import { normalizeTasks } from '#/routes/tasks/-lib/task-record'
import type { TaskRecord, TaskStatus } from '#/routes/tasks/-lib/task-record'

export type TaskStatusFilter = Exclude<TaskStatus, 'unknown'> | 'all'

export type TaskTypeFilter =
  | 'compile'
  | 'add_resource'
  | 'add_skill'
  | 'admin_reindex'
  | 'connector_import'
  | 'legacy_cleanup'
  | 'legacy_migration'
  | 'session_commit'
  | 'snapshot_restore_reindex'
  | 'all'

export type TaskPage = {
  items: TaskRecord[]
  hasMore: boolean
  nextCursor: string | null
}

export async function fetchTasks(
  taskType: TaskTypeFilter,
  status: TaskStatusFilter,
  cursor: string | undefined,
  limit: number,
): Promise<TaskPage> {
  const result = await getOvResult<unknown>(
    getTasks({
      query: {
        limit,
        ...(cursor ? { cursor } : {}),
        ...(taskType === 'all' ? {} : { task_type: taskType }),
        ...(status === 'all' ? {} : { status }),
      },
    }),
  )
  const items = normalizeTasks(result).sort(
    (left, right) =>
      Number(right.created_at || 0) - Number(left.created_at || 0),
  )
  const page = result as { has_more?: boolean; next_cursor?: string | null }
  return {
    items,
    hasMore: page.has_more === true,
    nextCursor: page.next_cursor ?? null,
  }
}
