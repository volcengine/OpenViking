export type TaskDataScope = '24h' | 'all'

// The server validates this endpoint with `limit <= 200`.
export const TASK_QUERY_LIMIT = 200

export function buildTaskQuery(dataScope: TaskDataScope, taskType: string) {
  return {
    limit: TASK_QUERY_LIMIT,
    status: undefined,
    task_type: taskType === 'all' ? undefined : taskType,
    include_archived: dataScope === 'all' ? true : undefined,
  }
}
