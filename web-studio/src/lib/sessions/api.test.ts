import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchSessionMessages } from './api'

const {
  getSessionBySessionIdMock,
  getSessionIdArchiveByArchiveIdMock,
  getSessionIdContextMock,
} = vi.hoisted(() => ({
  getSessionBySessionIdMock: vi.fn(),
  getSessionIdArchiveByArchiveIdMock: vi.fn(),
  getSessionIdContextMock: vi.fn(),
}))

vi.mock('#/gen/ov-client/sdk.gen', () => ({
  deleteSessionBySessionId: vi.fn(),
  getSessionBySessionId: getSessionBySessionIdMock,
  getSessionIdArchiveByArchiveId: getSessionIdArchiveByArchiveIdMock,
  getSessionIdContext: getSessionIdContextMock,
  getSessions: vi.fn(),
  postBotV1Chat: vi.fn(),
  postSessions: vi.fn(),
  postSessionIdCommit: vi.fn(),
  postSessionIdExtract: vi.fn(),
  postSessionIdMessages: vi.fn(),
  postSessionIdUsed: vi.fn(),
}))

function response(result: unknown) {
  return Promise.resolve({
    data: { result, status: 'ok' },
    headers: {},
    status: 200,
  })
}

function message(id: string, text: string) {
  return {
    created_at: '2026-07-24T08:00:00Z',
    id,
    parts: [{ text, type: 'text' }],
    role: 'user',
  }
}

describe('fetchSessionMessages', () => {
  beforeEach(() => {
    getSessionBySessionIdMock.mockReset()
    getSessionIdArchiveByArchiveIdMock.mockReset()
    getSessionIdContextMock.mockReset()
  })

  it('combines completed archives with current context in chronological order', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 2 }))
    getSessionIdArchiveByArchiveIdMock
      .mockReturnValueOnce(
        response({
          archive_id: 'archive_001',
          messages: [message('1', 'one')],
        }),
      )
      .mockReturnValueOnce(
        response({
          archive_id: 'archive_002',
          messages: [message('2', 'two')],
        }),
      )
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('3', 'three')] }),
    )

    const result = await fetchSessionMessages('session-1')

    expect(result.map(({ id }) => id)).toEqual(['1', '2', '3'])
    expect(getSessionIdArchiveByArchiveIdMock).toHaveBeenNthCalledWith(1, {
      path: { archive_id: 'archive_001', session_id: 'session-1' },
    })
    expect(getSessionIdArchiveByArchiveIdMock).toHaveBeenNthCalledWith(2, {
      path: { archive_id: 'archive_002', session_id: 'session-1' },
    })
  })

  it('keeps readable history when an unfinished archive is unavailable', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 2 }))
    getSessionIdArchiveByArchiveIdMock
      .mockRejectedValueOnce(new Error('archive not found'))
      .mockReturnValueOnce(
        response({
          archive_id: 'archive_002',
          messages: [message('2', 'two')],
        }),
      )
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('3', 'three')] }),
    )

    const result = await fetchSessionMessages('session-1')

    expect(result.map(({ id }) => id)).toEqual(['2', '3'])
  })
})
