import { normalizeReadContent } from '#/shared/lib/viking-fs'
import { getContentRead } from '#/gen/ov-client/sdk.gen'
import i18n from '#/i18n/core'
import {
  createOvClient,
  getOvResult,
  normalizeOvClientError,
  ovClient,
  postPackImport,
  postResourcesTempUpload,
} from '#/shared/api/ov-client/index'
import type { OvClientAdapter } from '#/shared/api/ov-client/types'
import { createPatch } from 'diff'

export interface SnapshotCommit {
  oid: string
  message: string
  author?: { name?: string; time_seconds?: number }
}

export interface SnapshotComparison {
  diff: string
  missingBase: boolean
  missingTarget: boolean
}

export function createDataManagementClient(): OvClientAdapter {
  return createOvClient({
    ...ovClient.getOptions(),
    apiKeyStorageKey: '',
    connection: ovClient.getConnection(),
  })
}

export async function listSnapshots(
  source: OvClientAdapter,
  uri: string,
  signal: AbortSignal,
  limit = 20,
): Promise<SnapshotCommit[]> {
  try {
    return await getOvResult<SnapshotCommit[]>(
      source.client.get({
        url: '/api/v1/snapshot/log',
        query: { branch: 'main', limit, paths: [uri] },
        signal,
      }),
    )
  } catch (error) {
    if (normalizeOvClientError(error).statusCode === 404) return []
    throw error
  }
}

export async function commitSnapshot(
  source: OvClientAdapter,
  uri: string,
  message: string,
  signal: AbortSignal,
): Promise<{ result: string; commit_oid: string }> {
  return getOvResult(
    source.client.post({
      url: '/api/v1/snapshot/commit',
      body: { message, paths: [uri], branch: 'main' },
      signal,
    }),
  )
}

export async function diffSnapshot(
  source: OvClientAdapter,
  uri: string,
  to: string,
  from: string | undefined,
  signal: AbortSignal,
): Promise<SnapshotComparison> {
  try {
    const result = await getOvResult<{
      diff_text: string
      change_type?: 'added' | 'deleted' | 'modified' | 'unchanged'
    }>(
      source.client.get({
        url: '/api/v1/snapshot/diff',
        query: { path: uri, to, ...(from ? { from } : {}) },
        signal,
      }),
    )
    return {
      diff: result.diff_text,
      missingBase: !from || result.change_type === 'added',
      missingTarget: result.change_type === 'deleted',
    }
  } catch (error) {
    // Hosted connections expose snapshot/show but may not route snapshot/diff.
    if (normalizeOvClientError(error).statusCode !== 404) throw error
    const [older, newer] = await Promise.all([
      from
        ? readSnapshotFile(source, uri, from, signal)
        : Promise.resolve({ content: '', missing: true }),
      readSnapshotFile(source, uri, to, signal),
    ])
    return {
      diff: createPatch(uri, older.content, newer.content),
      missingBase: older.missing,
      missingTarget: newer.missing,
    }
  }
}

async function isMissingSnapshotBlob(
  error: unknown,
  uri: string,
): Promise<boolean> {
  const normalized = normalizeOvClientError(error)
  if (normalized.statusCode !== 404) return false
  let code = normalized.code
  let details: unknown = normalized.details
  if (normalized.responseBody instanceof Blob) {
    try {
      const body: unknown = JSON.parse(await normalized.responseBody.text())
      if (body !== null && typeof body === 'object' && 'error' in body) {
        const payload = body.error
        if (payload !== null && typeof payload === 'object') {
          if ('code' in payload && typeof payload.code === 'string')
            code = payload.code
          if ('details' in payload) details = payload.details
        }
      }
    } catch {
      return false
    }
  }
  return (
    code === 'NOT_FOUND' &&
    details !== null &&
    typeof details === 'object' &&
    'type' in details &&
    details.type === 'git_blob' &&
    'resource' in details &&
    details.resource === uri
  )
}

async function readSnapshotFile(
  source: OvClientAdapter,
  uri: string,
  ref: string,
  signal: AbortSignal,
): Promise<{ content: string; missing: boolean }> {
  try {
    const response = await source.client.get({
      url: '/api/v1/snapshot/show',
      query: { target_ref: ref, path: uri, raw: true },
      responseType: 'blob',
      signal,
    })
    if (!(response.data instanceof Blob))
      throw new Error(i18n.t('dataManagement:invalidSnapshotContent'))
    return { content: await response.data.text(), missing: false }
  } catch (error) {
    if (!(await isMissingSnapshotBlob(error, uri))) throw error
    // The show endpoint maps a missing ref and a missing blob to the same
    // git_blob error. Confirm this ref still exists before treating it as empty.
    const commits = await listSnapshots(source, uri, signal, 500)
    if (!commits.some((commit) => commit.oid === ref)) throw error
    return { content: '', missing: true }
  }
}

export async function diffSnapshotWithCurrent(
  source: OvClientAdapter,
  uri: string,
  from: string,
  signal: AbortSignal,
): Promise<SnapshotComparison> {
  const [savedContent, current] = await Promise.all([
    from
      ? readSnapshotFile(source, uri, from, signal)
      : Promise.resolve({ content: '', missing: true }),
    getOvResult<unknown>(
      getContentRead({
        client: source.client,
        query: { uri, offset: 0, limit: -1, raw: true } as Parameters<
          typeof getContentRead
        >[0]['query'] & { raw: boolean },
        signal,
      }),
    ),
  ])
  return {
    diff: createPatch(uri, savedContent.content, normalizeReadContent(current)),
    missingBase: savedContent.missing,
    missingTarget: false,
  }
}

export async function exportOvpack(
  source: OvClientAdapter,
  uri: string,
  signal: AbortSignal,
): Promise<Blob> {
  const response = await source.client.post({
    url: '/api/v1/pack/export',
    body: { uri, include_vectors: false },
    responseType: 'blob',
    signal,
  })
  const data: unknown = response.data
  if (!(data instanceof Blob) || data.size === 0) {
    throw new Error(i18n.t('dataManagement:invalidExport'))
  }
  return data
}

export async function importOvpack(
  source: OvClientAdapter,
  parent: string,
  file: File,
  onConflict: 'fail' | 'skip' | 'overwrite',
  signal: AbortSignal,
): Promise<{ uri: string }> {
  const upload = await getOvResult<{ temp_file_id: string }>(
    postResourcesTempUpload({
      client: source.client,
      body: { file },
      signal,
    }),
  )
  if (!upload.temp_file_id)
    throw new Error(i18n.t('dataManagement:missingUploadId'))
  return getOvResult(
    postPackImport({
      client: source.client,
      body: {
        temp_file_id: upload.temp_file_id,
        parent,
        on_conflict: onConflict,
        vector_mode: 'auto',
      },
      signal,
    }),
  )
}
