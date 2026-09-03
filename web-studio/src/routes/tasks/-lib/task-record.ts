import type { TaskTimestamp } from './task-time'

export type TaskStatus =
  | 'cancelled'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'unknown'

export type TaskRecord = TaskTimestamp & {
  error?: string | null
  error_info?: {
    action?: string
    code?: string
    retryability?: 'manual' | 'requires_change' | 'retryable'
  } | null
  operation_id?: string | null
  owner_account_id?: string | null
  owner_user_id?: string | null
  parent_task_id?: string | null
  attempt_number?: number
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
    status === 'cancelled' ||
    status === 'cancelling' ||
    status === 'completed' ||
    status === 'failed' ||
    status === 'pending' ||
    status === 'running'
  ) {
    return status
  }
  return 'unknown'
}

export function isActiveTaskStatus(status: string | undefined): boolean {
  const normalized = normalizeTaskStatus(status)
  return (
    normalized === 'pending' ||
    normalized === 'running' ||
    normalized === 'cancelling'
  )
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

export function hasTaskFailureGuidance(task: TaskRecord): boolean {
  return Boolean(task.error || task.error_info?.code || task.error_info?.action)
}

export function getTaskFailureCode(
  errorInfo: TaskRecord['error_info'],
): string {
  return errorInfo?.code || 'TASK_FAILURE'
}

export function getTaskFailureGuidance(
  errorInfo: TaskRecord['error_info'],
  language: string,
): string {
  if (!language.startsWith('zh')) {
    return errorInfo?.action || 'Review the error details before retrying.'
  }

  const localized: Record<string, string> = {
    AUTH_EXPIRED:
      '模型服务凭据已过期。请更新对应供应商的 API Key 或令牌、重启服务使配置生效后，再重试。',
    ACCOUNT_OVERDUE: '模型服务商账户余额或账单异常。请处理后再重试。',
    MEDIA_SPARSE_INPUT_UNSUPPORTED:
      '稀疏向量仅支持文本输入。请先完成媒体文本提取或调整向量配置后再重试。',
    INPUT_SHAPE_INVALID: '会话消息格式不正确。请修正输入后再重试。',
    SOURCE_MISSING: '原始来源已不存在。请恢复或替换来源后再重试。',
    TRANSIENT_UPSTREAM: '上游服务暂时繁忙或超时，可稍后重试。',
  }
  return (
    localized[errorInfo?.code || ''] ||
    errorInfo?.action ||
    '请先查看失败详情并处理后再重试。'
  )
}
