import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  diffSnapshot,
  diffSnapshotWithCurrent,
  exportOvpack,
  importOvpack,
  listSnapshots,
} from '#/features/workbench/filesystem/lib/data-management'
import type { OvClientAdapter } from '#/shared/api/ov-client/types'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  tempUpload: vi.fn(),
  packImport: vi.fn(),
}))

vi.mock('#/shared/api/ov-client/index', () => ({
  createOvClient: vi.fn(),
  ovClient: { getOptions: vi.fn(), getConnection: vi.fn() },
  normalizeOvClientError: (error: unknown) => error,
  getOvResult: async (request: Promise<unknown>) => {
    const response = (await request) as { data?: { result?: unknown } }
    return response.data?.result
  },
  postResourcesTempUpload: mocks.tempUpload,
  postPackImport: mocks.packImport,
}))

const uri = 'viking://resources/docs/'
const signal = new AbortController().signal
const source = {
  client: { get: mocks.get, post: mocks.post },
} as unknown as OvClientAdapter

beforeEach(() => {
  vi.clearAllMocks()
})

describe('data management API', () => {
  it('scopes snapshot history to the selected path', async () => {
    mocks.get.mockResolvedValue({ data: { result: [{ oid: 'one' }] } })

    await expect(listSnapshots(source, uri, signal)).resolves.toEqual([
      { oid: 'one' },
    ])
    expect(mocks.get).toHaveBeenCalledWith({
      url: '/api/v1/snapshot/log',
      query: { branch: 'main', limit: 20, paths: [uri] },
      signal,
    })
  })

  it('loads more history for version selection and compares chosen refs', async () => {
    mocks.get.mockResolvedValue({ data: { result: [] } })
    await listSnapshots(source, uri, signal, 500)
    expect(mocks.get).toHaveBeenCalledWith({
      url: '/api/v1/snapshot/log',
      query: { branch: 'main', limit: 500, paths: [uri] },
      signal,
    })

    mocks.get.mockResolvedValue({ data: { result: { diff_text: 'diff' } } })
    await expect(
      diffSnapshot(source, uri, 'target-oid', 'base-oid', signal),
    ).resolves.toEqual({
      diff: 'diff',
      missingBase: false,
      missingTarget: false,
    })
    expect(mocks.get).toHaveBeenLastCalledWith({
      url: '/api/v1/snapshot/diff',
      query: { path: uri, to: 'target-oid', from: 'base-oid' },
      signal,
    })
  })

  it('compares saved versions through show when the diff route is unavailable', async () => {
    mocks.get
      .mockRejectedValueOnce({ statusCode: 404, message: 'Not found' })
      .mockResolvedValueOnce({ data: new Blob(['old\n']) })
      .mockResolvedValueOnce({ data: new Blob(['new\n']) })

    const comparison = await diffSnapshot(
      source,
      uri,
      'new-oid',
      'old-oid',
      signal,
    )
    expect(comparison.diff).toContain('-old')
    expect(comparison.diff).toContain('+new')
    expect(comparison).toMatchObject({
      missingBase: false,
      missingTarget: false,
    })
    expect(mocks.get).toHaveBeenNthCalledWith(2, {
      url: '/api/v1/snapshot/show',
      query: { target_ref: 'old-oid', path: uri, raw: true },
      responseType: 'blob',
      signal,
    })
    expect(mocks.get).toHaveBeenNthCalledWith(3, {
      url: '/api/v1/snapshot/show',
      query: { target_ref: 'new-oid', path: uri, raw: true },
      responseType: 'blob',
      signal,
    })
  })

  it('compares a saved snapshot with the live file content', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: new Blob(['old\n']) })
      .mockResolvedValueOnce({ data: { result: { content: 'new\n' } } })
    const comparison = await diffSnapshotWithCurrent(
      source,
      'viking://resources/docs/a.md',
      'saved-oid',
      signal,
    )
    expect(comparison.diff).toContain('-old')
    expect(comparison.diff).toContain('+new')
    expect(comparison).toMatchObject({
      missingBase: false,
      missingTarget: false,
    })
    expect(mocks.get).toHaveBeenNthCalledWith(1, {
      url: '/api/v1/snapshot/show',
      query: {
        target_ref: 'saved-oid',
        path: 'viking://resources/docs/a.md',
        raw: true,
      },
      responseType: 'blob',
      signal,
    })
  })

  it('marks a deleted target from the native diff response', async () => {
    mocks.get.mockResolvedValue({
      data: { result: { diff_text: '-removed', change_type: 'deleted' } },
    })
    await expect(
      diffSnapshot(source, uri, 'deleted-oid', 'old-oid', signal),
    ).resolves.toEqual({
      diff: '-removed',
      missingBase: false,
      missingTarget: true,
    })
  })

  it('treats a missing snapshot blob as an empty comparison side', async () => {
    const missing = {
      statusCode: 404,
      code: 'NOT_FOUND',
      details: { type: 'git_blob', resource: uri },
    }
    mocks.get
      .mockRejectedValueOnce({ statusCode: 404 }) // diff route unavailable
      .mockRejectedValueOnce(missing) // base version has no file
      .mockResolvedValueOnce({ data: new Blob(['new\n']) })
      .mockResolvedValueOnce({
        data: { result: [{ oid: 'old-oid' }, { oid: 'new-oid' }] },
      })
    const result = await diffSnapshot(source, uri, 'new-oid', 'old-oid', signal)
    expect(result.diff).toContain('+new')
    expect(result).toMatchObject({ missingBase: true, missingTarget: false })
  })

  it('reads a missing-file error returned as a binary response', async () => {
    mocks.get
      .mockRejectedValueOnce({ statusCode: 404 })
      .mockRejectedValueOnce({
        statusCode: 404,
        code: 'HTTP_ERROR',
        responseBody: new Blob([
          JSON.stringify({
            error: {
              code: 'NOT_FOUND',
              details: { type: 'git_blob', resource: uri },
            },
          }),
        ]),
      })
      .mockResolvedValueOnce({ data: new Blob(['new\n']) })
      .mockResolvedValueOnce({ data: { result: [{ oid: 'old-oid' }] } })
    const result = await diffSnapshot(source, uri, 'new-oid', 'old-oid', signal)
    expect(result).toMatchObject({ missingBase: true, missingTarget: false })
  })

  it('does not mistake an invalid ref for a missing file', async () => {
    const missing = {
      statusCode: 404,
      code: 'NOT_FOUND',
      details: { type: 'git_blob', resource: uri },
    }
    mocks.get
      .mockRejectedValueOnce({ statusCode: 404 })
      .mockRejectedValueOnce(missing)
      .mockResolvedValueOnce({ data: new Blob(['new\n']) })
      .mockResolvedValueOnce({ data: { result: [{ oid: 'new-oid' }] } })
    await expect(
      diffSnapshot(source, uri, 'new-oid', 'invalid-oid', signal),
    ).rejects.toBe(missing)
  })

  it('compares current content when the saved version lacks the file', async () => {
    const fileUri = 'viking://resources/docs/a.md'
    mocks.get
      .mockRejectedValueOnce({
        statusCode: 404,
        code: 'NOT_FOUND',
        details: { type: 'git_blob', resource: fileUri },
      })
      .mockResolvedValueOnce({ data: { result: { content: 'current\n' } } })
      .mockResolvedValueOnce({ data: { result: [{ oid: 'saved-oid' }] } })
    const result = await diffSnapshotWithCurrent(
      source,
      fileUri,
      'saved-oid',
      signal,
    )
    expect(result.diff).toContain('+current')
    expect(result).toMatchObject({ missingBase: true, missingTarget: false })
  })

  it('downloads binary OVPack and imports through the same source client', async () => {
    const blob = new Blob(['pack'], { type: 'application/zip' })
    mocks.post.mockResolvedValue({ data: blob })
    await expect(exportOvpack(source, uri, signal)).resolves.toBe(blob)
    expect(mocks.post).toHaveBeenCalledWith({
      url: '/api/v1/pack/export',
      body: { uri, include_vectors: false },
      responseType: 'blob',
      signal,
    })

    const file = new File(['pack'], 'docs.ovpack')
    mocks.tempUpload.mockResolvedValue({
      data: { result: { temp_file_id: 'temp-1' } },
    })
    mocks.packImport.mockResolvedValue({
      data: { result: { uri: 'viking://resources/docs/' } },
    })
    await importOvpack(source, uri, file, 'fail', signal)
    expect(mocks.tempUpload).toHaveBeenCalledWith({
      client: source.client,
      body: { file },
      signal,
    })
    expect(mocks.packImport).toHaveBeenCalledWith({
      client: source.client,
      body: {
        temp_file_id: 'temp-1',
        parent: uri,
        on_conflict: 'fail',
        vector_mode: 'auto',
      },
      signal,
    })
  })
})
