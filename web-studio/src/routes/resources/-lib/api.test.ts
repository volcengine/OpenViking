import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchDirectorySidecarContent, fetchFsList, fetchFsStat } from './api'

const { getContentReadMock, getFsLsMock, getFsStatMock } = vi.hoisted(() => ({
  getContentReadMock: vi.fn(),
  getFsLsMock: vi.fn(),
  getFsStatMock: vi.fn(),
}))

vi.mock('#/lib/ov-client', async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    getContentRead: getContentReadMock,
    getFsLs: getFsLsMock,
    getFsStat: getFsStatMock,
  }
})

beforeEach(() => {
  getContentReadMock.mockReset()
  getFsLsMock.mockReset()
  getFsStatMock.mockReset()
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

describe('fetchFsList', () => {
  it('requests newest entries before the server applies node_limit', async () => {
    await fetchFsList('viking://session', { nodeLimit: 200 })

    expect(getFsLsMock).toHaveBeenCalledWith({
      query: expect.objectContaining({
        node_limit: 200,
        sort_by: 'mtime',
        sort_order: 'desc',
      }),
    })
  })
})

it.each(['2030-10-10T00:00:00Z', null, undefined])(
  'preserves expiry %s in file and directory stat previews',
  async (expiresAt) => {
    getFsStatMock.mockResolvedValue({
      data: { status: 'ok', result: { isDir: true, expires_at: expiresAt } },
      headers: {},
      status: 200,
    })
    const entry = await fetchFsStat(
      'viking://user/alice/memories/events/2030/10/01/',
    )
    expect(entry.expiresAt).toBe(expiresAt)
  },
)
