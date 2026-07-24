import { beforeEach, describe, expect, it, vi } from 'vitest'

import type * as OvClientModule from '#/lib/ov-client'

import { deleteFile, fetchFsList } from './api'

const { deleteFsMock, getFsLsMock } = vi.hoisted(() => ({
  deleteFsMock: vi.fn(),
  getFsLsMock: vi.fn(),
}))

vi.mock('#/lib/ov-client', async (importOriginal) => {
  const original = await importOriginal<typeof OvClientModule>()
  return {
    ...original,
    deleteFs: deleteFsMock,
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

describe('deleteFile', () => {
  beforeEach(() => {
    deleteFsMock.mockReset()
    deleteFsMock.mockResolvedValue({
      data: { status: 'ok', result: null },
      headers: {},
      status: 200,
    })
  })

  it('deletes only the selected file and never enables recursive removal', async () => {
    await deleteFile('viking://resources/notes.md')

    expect(deleteFsMock).toHaveBeenCalledWith({
      query: {
        uri: 'viking://resources/notes.md',
        recursive: false,
      },
    })
  })
})
