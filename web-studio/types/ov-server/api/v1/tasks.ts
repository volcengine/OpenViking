export type TaskStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | (string & {})

export type TaskRecord = {
  task_id: string
  task_type: string
  status: TaskStatus
  created_at?: number
  updated_at?: number
  created_at_iso?: string
  updated_at_iso?: string
  resource_id?: string | null
  result?: Record<string, unknown> | null
  error?: string | null
}

export type TaskListResult = TaskRecord[]
