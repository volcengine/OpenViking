import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Message } from './types/message'

const sdkMocks = vi.hoisted(() => ({
  deleteSessionBySessionId: vi.fn(),
  getSessionBySessionId: vi.fn(),
  getSessionIdArchiveByArchiveId: vi.fn(),
  getSessionIdContext: vi.fn(),
  getSessions: vi.fn(),
  postBotV1Chat: vi.fn(),
  postSessions: vi.fn(),
  postSessionIdCommit: vi.fn(),
  postSessionIdExtract: vi.fn(),
  postSessionIdMessages: vi.fn(),
  postSessionIdUsed: vi.fn(),
}))

vi.mock('#/gen/ov-client/sdk.gen', () => sdkMocks)

vi.mock('#/lib/ov-client', () => ({
  OvClientError: class OvClientError extends Error {},
  getOvResult: async <T>(value: T | Promise<T>): Promise<T> => value,
  normalizeOvClientError: (error: unknown) => error,
  ovClient: {
    instance: {
      get: vi.fn(),
    },
  },
}))

import { fetchSessionMessages } from './api'

function message(id: string, text: string): Message {
  return {
    created_at: `2026-06-29T00:00:0${id.length}Z`,
    id,
    parts: [{ type: 'text', text }],
    role: id.startsWith('assistant') ? 'assistant' : 'user',
  }
}

describe('fetchSessionMessages', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('merges all readable archive messages before live messages', async () => {
    sdkMocks.getSessionIdContext.mockResolvedValue({
      messages: [message('live-1', 'live')],
    })
    sdkMocks.getSessionBySessionId.mockResolvedValue({ commit_count: 3 })
    sdkMocks.getSessionIdArchiveByArchiveId.mockImplementation(({ path }) => {
      if (path.archive_id === 'archive_002') {
        return Promise.reject(new Error('pending archive'))
      }
      return Promise.resolve({
        messages:
          path.archive_id === 'archive_001'
            ? [message('archive-1', 'first')]
            : [message('archive-3', 'third')],
      })
    })

    const messages = await fetchSessionMessages('session-1')

    expect(messages.map((item) => item.id)).toEqual([
      'archive-1',
      'archive-3',
      'live-1',
    ])
    expect(sdkMocks.getSessionIdArchiveByArchiveId).toHaveBeenCalledTimes(3)
  })

  it('deduplicates messages returned by archives and context', async () => {
    sdkMocks.getSessionIdContext.mockResolvedValue({
      messages: [message('shared', 'live copy'), message('live-2', 'live')],
    })
    sdkMocks.getSessionBySessionId.mockResolvedValue({ commit_count: 1 })
    sdkMocks.getSessionIdArchiveByArchiveId.mockResolvedValue({
      messages: [message('shared', 'archived copy')],
    })

    const messages = await fetchSessionMessages('session-1')

    expect(messages.map((item) => item.id)).toEqual(['shared', 'live-2'])
    expect(messages[0].parts).toEqual([{ type: 'text', text: 'archived copy' }])
  })
})
