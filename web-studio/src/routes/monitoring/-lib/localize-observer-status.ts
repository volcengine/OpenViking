import type { TFunction } from 'i18next'

import type { ObserverStatusBlock } from './parse-status'

type MonitoringTranslator = TFunction<'monitoringPage'>

const headerKeys = {
  'Avg (ms)': 'detail.columns.averageMs',
  Calls: 'detail.columns.calls',
  Collection: 'detail.columns.collection',
  Completion: 'detail.columns.completion',
  'Context Type': 'detail.columns.contextType',
  Count: 'detail.columns.count',
  Errors: 'queue.errors',
  'In Progress': 'queue.processing',
  'Index Count': 'detail.columns.indexCount',
  'Last Updated': 'detail.columns.lastUpdated',
  'Max (ms)': 'detail.columns.maximumMs',
  Metric: 'detail.columns.metric',
  'Min (ms)': 'detail.columns.minimumMs',
  Model: 'detail.columns.model',
  Operation: 'detail.columns.operation',
  Pending: 'queue.pending',
  Processed: 'queue.processed',
  Prompt: 'detail.columns.prompt',
  Provider: 'detail.columns.provider',
  Queries: 'detail.columns.queries',
  Queue: 'queue.queueName',
  Requeued: 'queue.requeued',
  Status: 'detail.columns.status',
  Total: 'detail.columns.total',
  Value: 'detail.columns.value',
  'Vector Count': 'detail.columns.vectorCount',
} as const

const metricKeys = {
  'Avg Latency (ms)': 'detail.metrics.averageLatency',
  'Avg Results/Query': 'detail.metrics.averageResults',
  'Avg Score': 'detail.metrics.averageScore',
  'Max Latency (ms)': 'detail.metrics.maximumLatency',
  'Overall Avg (ms)': 'detail.metrics.overallAverage',
  'Rerank Fallback': 'detail.metrics.rerankFallback',
  'Rerank Used': 'detail.metrics.rerankUsed',
  'Score Range': 'detail.metrics.scoreRange',
  'Total Operations': 'detail.metrics.totalOperations',
  'Total Queries': 'detail.metrics.totalQueries',
  'Total Results': 'detail.metrics.totalResults',
  'Total Time (s)': 'detail.metrics.totalTime',
  'Zero-Result Queries': 'detail.metrics.zeroResultQueries',
  'Zero-Result Rate': 'detail.metrics.zeroResultRate',
} as const

const queueKeys = {
  add_resource: 'queue.addResource',
  addresource: 'queue.addResource',
  embedding: 'queue.embedding',
  external_parse: 'queue.externalParse',
  externalparse: 'queue.externalParse',
  semantic: 'queue.semantic',
  'semantic-nodes': 'queue.semanticNodes',
  semantic_nodes: 'queue.semanticNodes',
  session_commit: 'queue.sessionCommit',
  sessioncommit: 'queue.sessionCommit',
  total: 'queue.totalRow',
  user_deletion: 'queue.userDeletion',
  userdeletion: 'queue.userDeletion',
} as const

const statusTextKeys = {
  'Embedding Models:': 'detail.statusText.embeddingModels',
  'No collections found.': 'detail.statusText.noCollections',
  'No filesystem statistics available.':
    'detail.statusText.filesystemUnavailable',
  'No model usage data available.': 'detail.statusText.modelUsageUnavailable',
  'No operation statistics recorded yet.':
    'detail.statusText.noFilesystemOperations',
  'No queue status data available.': 'detail.statusText.queueUnavailable',
  'No retrieval queries recorded.': 'detail.statusText.retrievalUnavailable',
  'Not initialized': 'detail.statusText.notInitialized',
  'Rerank Models:': 'detail.statusText.rerankModels',
  'VikingDB manager not initialized.': 'detail.statusText.vikingdbUnavailable',
  'VLM Models:': 'detail.statusText.vlmModels',
} as const

function translateMappedValue(
  value: string,
  mapping: Record<string, string>,
  t: MonitoringTranslator,
): string {
  const key = mapping[value]
  return key ? t(key) : value
}

function localizeCell(
  cell: string,
  header: string | undefined,
  t: MonitoringTranslator,
): string {
  if (header === 'Queue') {
    return translateMappedValue(cell.toLowerCase(), queueKeys, t)
  }
  if (header === 'Metric') return translateMappedValue(cell, metricKeys, t)
  if (header === 'Collection' && cell === 'TOTAL') {
    return t('detail.values.total')
  }

  if (header === 'Status') {
    if (cell === 'OK') return t('detail.values.ok')
    if (cell === 'ERROR') return t('detail.values.error')
    if (cell === 'configured') return t('detail.values.configured')
  }

  if (
    cell === 'unknown' &&
    (header === 'Provider' || header === 'Context Type')
  ) {
    return t('detail.values.unknown')
  }

  return cell
}

function localizeText(value: string, t: MonitoringTranslator): string {
  const mapped = translateMappedValue(value, statusTextKeys, t)
  if (mapped !== value) return mapped

  const mount = value.match(/^Mount: (.+) \(plugin: (.+)\)$/)
  if (mount) {
    return t('detail.statusText.mount', {
      path: mount[1],
      plugin: mount[2],
    })
  }

  const filesystemError = value.match(
    /^Error retrieving filesystem statistics: (.+)$/,
  )
  if (filesystemError) {
    return t('detail.statusText.filesystemError', {
      error: filesystemError[1],
    })
  }

  const lockStatus = [
    [/^Active locks: (.+)$/, 'activeLocks'],
    [/^Waiting locks: (.+)$/, 'waitingLocks'],
    [/^Stale locks removed: (.+)$/, 'staleLocksRemoved'],
    [/^Conflicts: (.+)$/, 'conflicts'],
  ] as const
  for (const [pattern, key] of lockStatus) {
    const match = value.match(pattern)
    if (match) {
      return t(`detail.statusText.${key}`, { count: match[1] })
    }
  }

  return value
}

export function localizeObserverStatusBlocks(
  blocks: ObserverStatusBlock[],
  t: MonitoringTranslator,
): ObserverStatusBlock[] {
  return blocks.map((block) => {
    if (block.kind === 'text') {
      return { kind: 'text', value: localizeText(block.value, t) }
    }

    return {
      headers: block.headers.map((header) =>
        translateMappedValue(header, headerKeys, t),
      ),
      kind: 'table',
      rows: block.rows.map((row) =>
        row.map((cell, index) => localizeCell(cell, block.headers[index], t)),
      ),
    }
  })
}
