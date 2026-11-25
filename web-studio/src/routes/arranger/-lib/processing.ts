import i18n from '#/i18n/core'
import {
  getFsStat,
  getOvClient,
  getOvResult,
  normalizeOvClientError,
  unwrapOvResponse,
} from '#/shared/api/ov-client'
import { isActiveTaskStatus } from '#/features/workbench/tasks/lib/task-record'

import { isWithinRoot, normalizeVikingUri } from './uri'

type ProcessingTask = {
  status: string
  resource_id?: string | null
  task_type?: string
  meta?: {
    request?: { to?: string; from?: string[] | null }
  }
}

type TaskPage = {
  items: Array<ProcessingTask | null>
  has_more: boolean
  next_cursor: string | null
}

type LockEntry = {
  uri: string
  isLocked: boolean
}

function invalidProcessingStatus(): Error {
  return new Error(i18n.t('arranger:errors.invalidProcessingStatus'))
}

function relatedTaskUri(task: ProcessingTask, source: string): string | null {
  if (!isActiveTaskStatus(task.status)) return null
  const candidates =
    task.task_type === 'compile'
      ? [task.meta?.request?.to, ...(task.meta?.request?.from ?? [])]
      : [task.resource_id]
  for (const uri of candidates) {
    if (typeof uri !== 'string' || !uri.startsWith('viking://')) continue
    const normalized = normalizeVikingUri(uri)
    if (isWithinRoot(normalized, source) || isWithinRoot(source, normalized)) {
      return normalized
    }
  }
  return null
}

async function findActiveTaskUri(source: string): Promise<string | null> {
  const client = getOvClient()
  for (const status of ['pending', 'running', 'cancelling']) {
    let cursor: string | undefined
    const seen = new Set<string>()
    for (;;) {
      const page = await getOvResult<TaskPage | null>(
        client.instance.get('/api/v1/tasks', {
          baseURL: client.getOptions().baseUrl,
          params: {
            status,
            include_internal: true,
            pagination: 'cursor',
            limit: 200,
            ...(cursor ? { cursor } : {}),
          },
        }),
      )
      if (
        !page ||
        !Array.isArray(page.items) ||
        typeof page.has_more !== 'boolean'
      ) {
        throw invalidProcessingStatus()
      }
      for (const task of page.items) {
        if (!task || typeof task.status !== 'string') {
          throw invalidProcessingStatus()
        }
        const uri = relatedTaskUri(task, source)
        if (uri) return uri
      }
      if (!page.has_more) break
      if (
        !page.next_cursor ||
        typeof page.next_cursor !== 'string' ||
        seen.has(page.next_cursor)
      ) {
        throw invalidProcessingStatus()
      }
      cursor = page.next_cursor
      seen.add(cursor)
    }
  }
  return null
}

async function findLockedUri(source: string): Promise<string | null> {
  const stat = await getOvResult<{ isDir: boolean; isLocked: boolean } | null>(
    getFsStat({ query: { uri: source } }),
  )
  if (
    !stat ||
    typeof stat.isDir !== 'boolean' ||
    typeof stat.isLocked !== 'boolean'
  ) {
    throw invalidProcessingStatus()
  }
  if (stat.isLocked) return source
  if (!stat.isDir) return null

  const client = getOvClient()
  let offset = 0
  for (;;) {
    const response = await client.instance.get<{
      result: Array<LockEntry | null>
      has_more: boolean
    }>('/api/v1/fs/ls', {
      baseURL: client.getOptions().baseUrl,
      params: {
        uri: source,
        recursive: true,
        output: 'original',
        show_all_hidden: true,
        include_abstract: false,
        extra_fields: 'locked',
        limit: 200,
        offset,
      },
    })
    const entries = unwrapOvResponse<Array<LockEntry | null>>(response)
    if (
      !Array.isArray(entries) ||
      typeof response.data.has_more !== 'boolean'
    ) {
      throw invalidProcessingStatus()
    }
    for (const entry of entries) {
      if (
        !entry ||
        typeof entry.uri !== 'string' ||
        typeof entry.isLocked !== 'boolean' ||
        !isWithinRoot(entry.uri, source)
      ) {
        throw invalidProcessingStatus()
      }
      if (entry.isLocked) return normalizeVikingUri(entry.uri)
    }
    if (!response.data.has_more) return null
    if (!entries.length) throw invalidProcessingStatus()
    offset += entries.length
  }
}

/** Reject active processing before a transfer makes any changes. */
export async function assertResourceIdle(uri: string): Promise<void> {
  const source = normalizeVikingUri(uri)
  let processingUri: string | null
  try {
    processingUri =
      (await findLockedUri(source)) ?? (await findActiveTaskUri(source))
  } catch (error) {
    throw new Error(
      i18n.t('arranger:errors.processingCheckFailed', {
        detail: normalizeOvClientError(error).message,
      }),
      { cause: error },
    )
  }
  if (processingUri) {
    throw new Error(
      i18n.t('arranger:errors.resourceProcessing', { uri: processingUri }),
    )
  }
}
