import apiClient, { handleAPI, APIResponse } from './api'

// Monitoring data types
export interface SystemStatus {
  status: 'healthy' | 'warning' | 'error'
  message?: string
  uptime?: number
  last_check?: string
}

// Backend system response format (from /observer/system)
export interface BackendSystemResponse {
  is_healthy: boolean
  errors: string[]
  components: Record<string, {
    name: string
    is_healthy: boolean
    has_errors: boolean
    status: string
  }>
}

export interface QueueInfo {
  queue_name: string
  queue_length: number
  processing: boolean
  last_updated?: string
}

export interface QueueStats {
  embedding_queue?: QueueInfo
  semantic_queue?: QueueInfo
  other_queues?: Record<string, QueueInfo>
  [key: string]: any
}

export interface ResourceStats {
  total_resources: number
  total_size: number
  by_type?: Record<string, number>
  by_directory?: Record<string, number>
  recent_additions?: number
  [key: string]: any
}

export interface VikingDBCollection {
  collection: string
  index_count: number
  vector_count: number
  status: string
}

export interface VikingDBStatus {
  collections: number
  total_vectors: number
  storage_used: number
  collection_list?: VikingDBCollection[]
  query_performance?: {
    avg_latency_ms?: number
    queries_per_second?: number
  }
  [key: string]: any
}

export interface VLMModel {
  model: string
  provider: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  last_updated: string
}

export interface VLMStats {
  provider: string
  model: string
  models?: VLMModel[]
  token_usage?: {
    total_tokens?: number
    prompt_tokens?: number
    completion_tokens?: number
  }
  request_count?: number
  avg_response_time_ms?: number
  [key: string]: any
}

export interface TransactionStatus {
  is_healthy: boolean
  active_transactions: number
  message?: string
}

export interface SystemInfo {
  cpu_usage?: number
  memory_usage?: number
  disk_usage?: number
  active_sessions?: number
  active_tasks?: number
}

export interface MonitoringSummary {
  system: SystemStatus
  queue: QueueStats
  resources: ResourceStats
  vikingdb: VikingDBStatus
  vlm: VLMStats
  transaction: TransactionStatus
  systemInfo: SystemInfo
  last_updated: string
  [key: string]: any
}

// Parse ASCII table from Queue status
const parseQueueTable = (table: string): QueueInfo[] => {
  const lines = table.trim().split('\n')
  if (lines.length < 4) {
    return []
  }

  // Find header line (the one with column names)
  let headerLine = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('|     Queue') || lines[i].includes('|Queue|')) {
      headerLine = i
      break
    }
  }

  if (headerLine === -1 || headerLine + 2 >= lines.length) {
    return []
  }

  const results: QueueInfo[] = []

  // Start from line after separator (headerLine + 2) and parse until next separator or end
  for (let i = headerLine + 2; i < lines.length; i++) {
    const line = lines[i]
    // Stop if we hit a separator line (starts with +---)
    if (line.trim().startsWith('+') && line.includes('-')) {
      continue
    }

    const columns = line.split('|').map(col => col.trim()).filter(col => col !== '')

    // Expected columns: Queue, Pending, In Progress, Processed, Errors, Total
    if (columns.length >= 6) {
      // Skip TOTAL row
      if (columns[0] === 'TOTAL' || columns[0].toUpperCase() === 'TOTAL') {
        continue
      }

      const queueName = columns[0].toLowerCase().replace(/-/g, '_')
      results.push({
        queue_name: queueName,
        queue_length: parseInt(columns[1]) || 0, // Pending
        processing: (parseInt(columns[2]) || 0) > 0, // In Progress
        last_updated: new Date().toISOString()
      })
    }
  }

  return results
}

// Normalize queue status from table string
const normalizeQueueStats = (statusTable: string): QueueStats => {
  const queues = parseQueueTable(statusTable)
  const result: QueueStats = {}

  for (const q of queues) {
    if (q.queue_name === 'embedding') {
      result.embedding_queue = q
    } else if (q.queue_name === 'semantic') {
      result.semantic_queue = q
    } else {
      if (!result.other_queues) {
        result.other_queues = {}
      }
      result.other_queues[q.queue_name] = q
    }
  }

  // Ensure required queues exist
  if (!result.embedding_queue) {
    result.embedding_queue = { queue_name: 'embedding', queue_length: 0, processing: false }
  }
  if (!result.semantic_queue) {
    result.semantic_queue = { queue_name: 'semantic', queue_length: 0, processing: false }
  }

  return result
}

// Parse ASCII table from VikingDB status
const parseVikingDBTable = (table: string): VikingDBCollection[] => {
  const lines = table.trim().split('\n')
  if (lines.length < 4) {
    return []
  }

  // Find header line (the one with column names)
  let headerLine = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('| Collection |') || lines[i].includes('|Collection|')) {
      headerLine = i
      break
    }
  }

  if (headerLine === -1 || headerLine + 2 >= lines.length) {
    return []
  }

  const results: VikingDBCollection[] = []

  // Start from line after separator (headerLine + 2) and parse until next separator or end
  for (let i = headerLine + 2; i < lines.length; i++) {
    const line = lines[i]
    // Stop if we hit a separator line (starts with +---)
    if (line.trim().startsWith('+') && line.includes('-')) {
      continue
    }

    const columns = line.split('|').map(col => col.trim()).filter(col => col !== '')

    // Expected columns: Collection, Index Count, Vector Count, Status
    if (columns.length >= 4) {
      // Skip TOTAL row
      if (columns[0] === 'TOTAL' || columns[0].toUpperCase() === 'TOTAL') {
        continue
      }

      results.push({
        collection: columns[0],
        index_count: parseInt(columns[1]) || 0,
        vector_count: parseInt(columns[2]) || 0,
        status: columns[3] || 'Unknown'
      })
    }
  }

  return results
}

// Normalize VikingDB status from table string
const normalizeVikingDBStatus = (statusTable: string): VikingDBStatus => {
  const parsedCollections = parseVikingDBTable(statusTable)

  if (parsedCollections.length > 0) {
    let totalVectorCount = 0
    for (const col of parsedCollections) {
      totalVectorCount += col.vector_count
    }

    return {
      collections: parsedCollections.length,
      total_vectors: totalVectorCount,
      storage_used: 0,
      collection_list: parsedCollections,
      query_performance: {}
    }
  }

  return {
    collections: 0,
    total_vectors: 0,
    storage_used: 0,
    collection_list: [],
    query_performance: {}
  }
}

// Parse ASCII table from VLM status
const parseVLMTable = (table: string): VLMModel[] => {
  const lines = table.trim().split('\n')
  if (lines.length < 4) {
    return []
  }

  // Find header line (the one with column names)
  let headerLine = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('|    Model') || lines[i].includes('| Model |')) {
      headerLine = i
      break
    }
  }

  if (headerLine === -1 || headerLine + 2 >= lines.length) {
    return []
  }

  const results: VLMModel[] = []

  // Start from line after separator (headerLine + 2) and parse until next separator or end
  for (let i = headerLine + 2; i < lines.length; i++) {
    const line = lines[i]
    // Stop if we hit a separator line (starts with +---)
    if (line.trim().startsWith('+') && line.includes('-')) {
      continue
    }

    const columns = line.split('|').map(col => col.trim()).filter(col => col !== '')

    // Expected columns: Model, Provider, Prompt, Completion, Total, Last Updated
    if (columns.length >= 6) {
      // Skip TOTAL row
      if (columns[0] === 'TOTAL' || columns[0].toUpperCase() === 'TOTAL') {
        continue
      }

      results.push({
        model: columns[0],
        provider: columns[1],
        prompt_tokens: parseInt(columns[2]) || 0,
        completion_tokens: parseInt(columns[3]) || 0,
        total_tokens: parseInt(columns[4]) || 0,
        last_updated: columns[5]
      })
    }
  }

  return results
}

// Normalize VLM status from table string
const normalizeVLMStats = (statusTable: string): VLMStats => {
  // Check for empty or no data
  if (!statusTable || statusTable.includes('No token usage data available')) {
    return {
      provider: '',
      model: '',
      models: [],
      token_usage: { total_tokens: 0 },
      request_count: 0
    }
  }

  const parsedModels = parseVLMTable(statusTable)

  if (parsedModels.length > 0) {
    const firstModel = parsedModels[0]
    let totalPromptTokens = 0
    let totalCompletionTokens = 0
    let totalAllTokens = 0

    for (const model of parsedModels) {
      totalPromptTokens += model.prompt_tokens
      totalCompletionTokens += model.completion_tokens
      totalAllTokens += model.total_tokens
    }

    return {
      provider: firstModel.provider,
      model: firstModel.model,
      models: parsedModels,
      token_usage: {
        prompt_tokens: totalPromptTokens,
        completion_tokens: totalCompletionTokens,
        total_tokens: totalAllTokens
      },
      request_count: parsedModels.length,
      avg_response_time_ms: undefined
    }
  }

  return {
    provider: '',
    model: '',
    models: [],
    token_usage: { total_tokens: 0 },
    request_count: 0
  }
}

// Normalize transaction status from status string
const normalizeTransactionStatus = (statusTable: string): TransactionStatus => {
  if (statusTable.includes('No active transactions')) {
    return {
      is_healthy: true,
      active_transactions: 0,
      message: 'No active transactions'
    }
  }

  // Try to parse transaction count if present
  const match = statusTable.match(/(\d+)\s*active?\s*transaction/i)
  if (match) {
    return {
      is_healthy: true,
      active_transactions: parseInt(match[1]) || 0,
      message: statusTable
    }
  }

  return {
    is_healthy: true,
    active_transactions: 0,
    message: statusTable
  }
}

// Monitoring service
export const monitoringService = {
  /**
   * Get all monitoring data from single /observer/system endpoint
   */
  getAll: async (): Promise<APIResponse<MonitoringSummary>> => {
    try {
      const response = await handleAPI<BackendSystemResponse>(
        apiClient.get('/observer/system')
      )

      if (!response.success || !response.data) {
        return {
          success: false,
          error: response.error || 'Failed to fetch monitoring data'
        }
      }

      const data = response.data
      const components = data.components || {}

      // Parse each component's status table
      const queueStats = components.queue
        ? normalizeQueueStats(components.queue.status)
        : { embedding_queue: { queue_name: 'embedding', queue_length: 0, processing: false }, semantic_queue: { queue_name: 'semantic', queue_length: 0, processing: false } }

      const vikingdbStats = components.vikingdb
        ? normalizeVikingDBStatus(components.vikingdb.status)
        : { collections: 0, total_vectors: 0, storage_used: 0, collection_list: [] }

      const vlmStats = components.vlm
        ? normalizeVLMStats(components.vlm.status)
        : { provider: '', model: '', models: [], token_usage: { total_tokens: 0 } }

      const transactionStats = components.transaction
        ? normalizeTransactionStatus(components.transaction.status)
        : { is_healthy: true, active_transactions: 0 }

      const summary: MonitoringSummary = {
        system: {
          status: data.is_healthy ? 'healthy' : (data.errors?.length > 0 ? 'error' : 'warning'),
          message: data.errors?.join(', ') || undefined
        },
        queue: queueStats,
        resources: { total_resources: 0, total_size: 0 },
        vikingdb: vikingdbStats,
        vlm: vlmStats,
        transaction: transactionStats,
        systemInfo: {},
        last_updated: new Date().toISOString()
      }

      return {
        success: true,
        data: summary
      }
    } catch (error) {
      const apiError = error as APIResponse
      return {
        success: false,
        error: apiError.error || 'Failed to fetch monitoring data'
      }
    }
  },

  /**
   * Get system health status
   */
  getSystemStatus: async (): Promise<APIResponse<SystemStatus>> => {
    return handleAPI<BackendSystemResponse>(
      apiClient.get('/observer/system')
    ).then(res => ({
      ...res,
      data: res.data ? {
        status: res.data.is_healthy ? 'healthy' : (res.data.errors?.length > 0 ? 'error' : 'warning'),
        message: res.data.errors?.join(', ') || undefined
      } : undefined
    }))
  },

  /**
   * Get resource statistics
   */
  getResourceStats: async (): Promise<APIResponse<ResourceStats>> => {
    return {
      success: true,
      data: {
        total_resources: 0,
        total_size: 0
      }
    }
  },

  /**
   * Get task statistics
   */
  getTaskStats: async (): Promise<APIResponse<{
    active: number
    completed: number
    failed: number
  }>> => {
    return handleAPI<any>(
      apiClient.get('/tasks')
    ).then(res => ({
      ...res,
      data: res.data ? {
        active: (res.data.filter((t: any) => t.status === 'running').length) || 0,
        completed: (res.data.filter((t: any) => t.status === 'completed').length) || 0,
        failed: (res.data.filter((t: any) => t.status === 'failed').length) || 0
      } : { active: 0, completed: 0, failed: 0 }
    }))
  }
}

export default monitoringService
