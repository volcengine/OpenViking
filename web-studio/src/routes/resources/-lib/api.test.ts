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
})
