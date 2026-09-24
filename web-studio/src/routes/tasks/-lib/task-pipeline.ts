import type { TaskRecord } from './task-record'

export type StepState = 'completed' | 'running' | 'pending' | 'failed'

export type PipelineTranslate = (key: string) => string

export type PipelineStep = {
  name: string
  state: StepState
  count?: number
}

function resolve(key: string, t?: PipelineTranslate): string {
  return t ? t(`pipeline.step.${key}`) : key
}

export type PipelineGroup =
  | { type: 'serial'; step: PipelineStep }
  | { type: 'parallel'; steps: PipelineStep[] }

type QueueStatus = Record<
  string,
  { error_count?: number; processed?: number } | undefined
>

function inferState(
  qKey: string | null,
  fallback: StepState,
  status: string | undefined,
  qStatus: QueueStatus | undefined,
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
  t?: PipelineTranslate,
): PipelineStep[] {
  const type = task.task_type
  const status = task.status
  const resObj = (task.result || {}) as Record<string, any>
  const qStatus = resObj.queue_status as QueueStatus | undefined

  if (type === 'session_commit') {
    return [
      {
        name: resolve('sessionPersistence', t),
        state: status === 'completed' ? 'completed' : (status as StepState),
      },
    ]
  }

  if (type === 'admin_reindex' || type === 'snapshot_restore_reindex') {
    return [
      {
        name: resolve('externalParse', t),
        state: status === 'pending' ? 'pending' : 'completed',
      },
      {
        name: resolve('embedding', t),
        state: inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus),
        count: resObj.reindexed_items,
      },
    ]
  }

  if (type === 'connector_import') {
    const preState: StepState = status === 'pending' ? 'pending' : 'completed'
    return [
      { name: resolve('connectorAuth', t), state: preState },
      { name: resolve('resourceFetching', t), state: preState, count: resObj.downloaded_files },
      { name: resolve('externalParse', t), state: inferState('Semantic', status === 'completed' ? 'completed' : (status as StepState), status, qStatus) },
      { name: resolve('embedding', t), state: inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus) },
    ]
  }

  // Default resource ingestion pipeline: 外部解析 -> 语义处理 -> 嵌入向量
  return [
    {
      name: resolve('externalParse', t),
      state: status === 'pending' ? 'pending' : 'completed',
    },
    {
      name: resolve('semantic', t),
      state: inferState('Semantic', status === 'completed' ? 'completed' : (status as StepState), status, qStatus),
      count: qStatus?.Semantic?.processed,
    },
    {
      name: resolve('embedding', t),
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
  t?: PipelineTranslate,
): PipelineGroup[] {
  const type = task.task_type
  const status = task.status
  const resObj = (task.result || {}) as Record<string, any>
  const qStatus = resObj.queue_status as QueueStatus | undefined

  if (type === 'session_commit') {
    return [
      {
        type: 'serial',
        step: {
          name: resolve('sessionCommit', t),
          state: status === 'completed' ? 'completed' : (status as StepState),
        },
      },
    ]
  }

  if (type === 'admin_reindex' || type === 'snapshot_restore_reindex') {
    const purgeState: StepState = status === 'pending' ? 'pending' : 'completed'
    const rebuildState = inferState('Embedding', status === 'completed' ? 'completed' : (status as StepState), status, qStatus)
    return [
      { type: 'serial', step: { name: resolve('externalParse', t), state: purgeState } },
      { type: 'serial', step: { name: resolve('embedding', t), state: rebuildState } },
    ]
  }

  if (type === 'connector_import') {
    const preState: StepState = status === 'pending' ? 'pending' : 'completed'
    const semState = inferState('Semantic', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
    const embState = inferState('Embedding', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
    return [
      { type: 'serial', step: { name: resolve('externalParse', t), state: preState } },
      {
        type: 'parallel',
        steps: [
          { name: resolve('semantic', t), state: semState },
          { name: resolve('embedding', t), state: embState },
        ],
      },
    ]
  }

  // Default resource ingestion pipeline: 外部解析 -> (语义处理 + 嵌入向量)
  const parseState: StepState = status === 'pending' ? 'pending' : 'completed'
  const semState = inferState('Semantic', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
  const embState = inferState('Embedding', status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'failed' ? 'failed' : 'pending', status, qStatus)
  return [
    { type: 'serial', step: { name: resolve('externalParse', t), state: parseState } },
    {
      type: 'parallel',
      steps: [
        { name: resolve('semantic', t), state: semState },
        { name: resolve('embedding', t), state: embState },
      ],
    },
  ]
}
