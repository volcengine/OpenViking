import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchFsList } from './api'

const { getFsLsMock } = vi.hoisted(() => ({
  getFsLsMock: vi.fn(),
}))

vi.mock('#/lib/ov-client', async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    getFsLs: getFsLsMock,
  }
})

describe('fetchFsList', () => {
  beforeEach(() => {
    getFsLsMock.mockReset()
    getFsLsMock.mockResolvedValue({
      data: { status: 'ok', result: [] },
      headers: {},
      status: 200,
    })
  })

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

  it('prefers logical session activity over directory mtime', async () => {
    getFsLsMock.mockResolvedValue({
      data: {
        status: 'ok',
        result: [
          {
            name: 'session-a',
            uri: 'viking://user/default/sessions/session-a',
            isDir: true,
            modTime: '2026-07-12T01:00:00Z',
            activityTime: '2026-07-14T01:00:00Z',
          },
        ],
      },
      headers: {},
      status: 200,
    })

    const result = await fetchFsList('viking://user/default/sessions')

    expect(result.entries[0]?.modTimestamp).toBe(
      Date.parse('2026-07-14T01:00:00Z'),
    )
  })
})
