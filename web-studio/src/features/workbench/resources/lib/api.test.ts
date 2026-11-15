import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteSharedResource,
  fetchDirectorySidecarContent,
  fetchFsList,
} from '#/features/workbench/resources/lib/api'

const { deleteResourcesMock, getContentReadMock, getFsLsMock } = vi.hoisted(
  () => ({
    deleteResourcesMock: vi.fn(),
    getContentReadMock: vi.fn(),
    getFsLsMock: vi.fn(),
  }),
)

vi.mock('#/shared/api/ov-client/index', async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    deleteResources: deleteResourcesMock,
    getContentRead: getContentReadMock,
    getFsLs: getFsLsMock,
  }
})

beforeEach(() => {
  deleteResourcesMock.mockReset()
  deleteResourcesMock.mockResolvedValue({
    data: { status: 'ok', result: { semantic_status: 'complete' } },
    headers: {},
    status: 200,
  })
  getContentReadMock.mockReset()
  getFsLsMock.mockReset()
  getFsLsMock.mockResolvedValue({
    data: { status: 'ok', result: [] },
    headers: {},
    status: 200,
  })
})

describe('fetchDirectorySidecarContent', () => {
  it.each(['abstract', 'overview'] as const)(
    'does not read a %s sidecar for the virtual root',
    async (level) => {
      await expect(
        fetchDirectorySidecarContent('viking://', level),
      ).resolves.toBe('')
      expect(getContentReadMock).not.toHaveBeenCalled()
    },
  )

  it('reads raw L0/L1 sidecars instead of the body-only semantic accessors', async () => {
    getContentReadMock.mockResolvedValue({
      data: {
        status: 'ok',
        result: '---\ndirectory: viking://resources/demo/\n---',
      },
      headers: {},
      status: 200,
    })

    await expect(
      fetchDirectorySidecarContent('viking://resources/demo/', 'abstract'),
    ).resolves.toContain('directory: viking://resources/demo/')
    expect(getContentReadMock).toHaveBeenCalledWith({
      query: {
        uri: 'viking://resources/demo/.abstract.md',
        offset: 0,
        limit: -1,
        raw: true,
      },
    })
  })
})

describe('deleteSharedResource', () => {
  it('uses the restricted endpoint and waits for index cleanup', async () => {
    await deleteSharedResource('viking://resources/project', true)

    expect(deleteResourcesMock).toHaveBeenCalledWith({
      query: { uri: 'viking://resources/project', recursive: true, wait: true },
    })
  })

  it('rejects non-shared URIs before sending a request', async () => {
    await expect(
      deleteSharedResource('viking://user/alice/resources/private', true),
    ).rejects.toThrow()
    expect(deleteResourcesMock).not.toHaveBeenCalled()
  })
})

describe('fetchFsList', () => {
  it('requests newest entries before the server applies node_limit', async () => {
    await fetchFsList('viking://~/sessions', { nodeLimit: 200 })

    expect(getFsLsMock).toHaveBeenCalledWith({
      query: expect.objectContaining({
        node_limit: 200,
        sort_by: 'mtime',
        sort_order: 'desc',
      }),
    })
  })
})
