import type { TaskRecord } from './task-record'

export type StepState = 'completed' | 'running' | 'pending' | 'failed'

export type PipelineStep = {
  name: string
  state: StepState
  count?: number
}

export type PipelineGroup =
  | { type: 'serial'; step: PipelineStep }
  | { type: 'parallel'; steps: PipelineStep[] }

function inferState(
  qKey: string | null,
  fallback: StepState,
  status: string | undefined,
  qStatus: Record<string, { error_count?: number; processed?: number }> | undefined,
): StepState {
  if (status === 'pending') return 'pending'
  if (qKey && qStatus?.[qKey]) {
    const s = qStatus[qKey]
    if ((s.error_count ?? 0) > 0) return 'failed'
    if ((s.processed ?? 0) > 0) return 'completed'
    return status === 'running' ? 'running' : fallback
  }
  return fallback
}

/**
 * Single Source of Truth (SSOT) for task pipeline steps list.
 * Used by Task Detail Sheet Drawer.
 */
export function getTaskPipelineSteps(
  task: TaskRecord,
  language: string = 'zh',
): PipelineStep[] {
  const isZh = language.startsWith('zh')
  const type = task.task_type
  const status = task.status
  const resObj = (task.result || {}) as Record<string, any>
  const qStatus = resObj.queue_status as Record<string, { error_count?: number; processed?: number }> | undefined

  if (type === 'session_commit') {
    return [
      {
        name: isZh ? '会话状态持久化' : 'Session Persistence',
        state: status === 'completed' ? 'completed' : (status as StepState),
      },
    ]
  }

  if (type === 'admin_reindex' || type === 'snapshot_restore_reindex') {
    return [
      {
        name: isZh ? '外部解析' : 'Document Parsing',
        state: status === 'pending' ? 'pending' : 'completed',
      },
      {
        name: isZh ? '嵌入向量' : 'Vector Embedding',
        state: inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus),
        count: resObj.reindexed_items,
      },
    ]
  }

  if (type === 'connector_import') {
    const preState: StepState = status === 'pending' ? 'pending' : 'completed'
    return [
      { name: isZh ? '连接器鉴权' : 'Connector Auth', state: preState },
      { name: isZh ? '资源拉取' : 'Resource Fetching', state: preState, count: resObj.downloaded_files },
      { name: isZh ? '外部解析' : 'Document Parsing', state: inferState('Semantic', status === 'completed' ? 'completed' : (status as StepState), status, qStatus) },
      { name: isZh ? '嵌入向量' : 'Vector Embedding', state: inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus) },
    ]
  }

  // Default resource ingestion pipeline: 外部解析 -> 语义处理 -> 嵌入向量
  return [
    {
      name: isZh ? '外部解析' : 'Document Parsing',
      state: status === 'pending' ? 'pending' : 'completed',
    },
    {
      name: isZh ? '语义处理' : 'Semantic Processing',
      state: inferState('Semantic', status === 'completed' ? 'completed' : (status as StepState), status, qStatus),
      count: qStatus?.Semantic?.processed,
    },
    {
      name: isZh ? '嵌入向量' : 'Vector Embedding',
      state: inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus),
      count: qStatus?.Embedding?.processed,
    },
  ]
}

/**
 * Single Source of Truth (SSOT) for task pipeline diagram groups.
 * Used by Task Table Row column "工序队列流转".
 */
export function getTaskPipelineGroups(
  task: TaskRecord,
  language: string = 'zh',
): PipelineGroup[] {
  const isZh = language.startsWith('zh')
  const type = task.task_type
  const status = task.status
  const resObj = (task.result || {}) as Record<string, any>
  const qStatus = resObj.queue_status as Record<string, { error_count?: number; processed?: number }> | undefined

  if (type === 'session_commit') {
    return [
      {
        type: 'serial',
        step: {
          name: isZh ? '会话提交' : 'Session Commit',
          state: status === 'completed' ? 'completed' : (status as StepState),
        },
      },
    ]
  }

  if (type === 'admin_reindex' || type === 'snapshot_restore_reindex') {
    const purgeState: StepState = status === 'pending' ? 'pending' : 'completed'
    const rebuildState = inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus)
    return [
      { type: 'serial', step: { name: isZh ? '外部解析' : 'Document Parsing', state: purgeState } },
      { type: 'serial', step: { name: isZh ? '嵌入向量' : 'Vector Embedding', state: rebuildState } },
    ]
  }

  if (type === 'connector_import') {
    const preState: StepState = status === 'pending' ? 'pending' : 'completed'
    const semState = inferState('Semantic', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
    const embState = inferState('Embedding', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
    return [
      { type: 'serial', step: { name: isZh ? '外部解析' : 'Document Parsing', state: preState } },
      {
        type: 'parallel',
        steps: [
          { name: isZh ? '语义处理' : 'Semantic Processing', state: semState },
          { name: isZh ? '嵌入向量' : 'Vector Embedding', state: embState },
        ],
      },
    ]
  }

  // Default resource ingestion pipeline: 外部解析 -> (语义处理 + 嵌入向量)
  const parseState: StepState = status === 'pending' ? 'pending' : 'completed'
  const semState = inferState('Semantic', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
  const embState = inferState('Embedding', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
  return [
    { type: 'serial', step: { name: isZh ? '外部解析' : 'Document Parsing', state: parseState } },
    {
      type: 'parallel',
      steps: [
        { name: isZh ? '语义处理' : 'Semantic Processing', state: semState },
        { name: isZh ? '嵌入向量' : 'Vector Embedding', state: embState },
      ],
    },
  ]
}
