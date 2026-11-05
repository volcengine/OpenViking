// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OvClientError, ovClient } from '#/shared/api/ov-client/index'

import {
  fetchBotHealth,
  fetchSessionFirstTitle,
  fetchSessionMessages,
  interruptChatSession,
  sendChatStream,
  serializeParts,
  steerChatSession,
} from '#/features/workbench/sessions/lib/api'

const {
  getSessionBySessionIdMock,
  getSessionIdArchiveByArchiveIdMock,
  getSessionIdContextMock,
  getContentReadMock,
} = vi.hoisted(() => ({
  getSessionBySessionIdMock: vi.fn(),
  getSessionIdArchiveByArchiveIdMock: vi.fn(),
  getSessionIdContextMock: vi.fn(),
  getContentReadMock: vi.fn(),
}))

vi.mock('#/gen/ov-client/sdk.gen', () => ({
  deleteSessionBySessionId: vi.fn(),
  getContentRead: getContentReadMock,
  getSessionBySessionId: getSessionBySessionIdMock,
  getSessionIdArchiveByArchiveId: getSessionIdArchiveByArchiveIdMock,
  getSessionIdContext: getSessionIdContextMock,
  getSessions: vi.fn(),
  postBotV1Chat: vi.fn(),
  postSessions: vi.fn(),
  postSessionIdCommit: vi.fn(),
  postSessionIdMessages: vi.fn(),
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
    getContentReadMock.mockReset()
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

  it('reads hosted current context without requesting optional archives', async () => {
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('current', 'readable')] }),
    )
    getSessionIdArchiveByArchiveIdMock.mockRejectedValue(
      new OvClientError({
        code: 'ApiBlocked',
        statusCode: 403,
        message: 'Not exposed',
      }),
    )
    const result = await fetchSessionMessages(
      'hosted-session',
      { commit_count: 2 } as Parameters<typeof fetchSessionMessages>[1],
      'none',
    )
    expect(result.map(({ id }) => id)).toEqual(['current'])
    expect(getSessionIdArchiveByArchiveIdMock).not.toHaveBeenCalled()
  })

  it('keeps readable current messages when context includes a system record', async () => {
    getSessionIdContextMock.mockReturnValue(
      response({
        messages: [
          { ...message('system', 'instructions'), role: 'system' },
          message('user', 'question'),
          { ...message('assistant', 'answer'), role: 'assistant' },
        ],
      }),
    )

    const result = await fetchSessionMessages('s', undefined, 'none')

    expect(result.map(({ id }) => id)).toEqual(['user', 'assistant'])
  })

  it.each(['api', 'content'] as const)(
    'keeps readable %s archive messages when history includes a system record',
    async (source) => {
      const messages = [
        message('user', 'question'),
        { ...message('system', 'instructions'), role: 'system' },
        { ...message('assistant', 'answer'), role: 'assistant' },
      ]
      getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
      getSessionIdArchiveByArchiveIdMock.mockReturnValue(response({ messages }))
      getContentReadMock.mockReturnValue(
        response(messages.map((row) => JSON.stringify(row)).join('\n')),
      )

      const result = await fetchSessionMessages(
        's',
        { commit_count: 1, uri: 'viking://user/u/sessions/s' } as Parameters<
          typeof fetchSessionMessages
        >[1],
        source,
      )

      expect(result.map(({ id }) => id)).toEqual(['user', 'assistant'])
    },
  )

  it('loads hosted archives through content read when current context is empty', async () => {
    getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
    getContentReadMock.mockReturnValue(
      response(
        [
          message('old-user', 'question'),
          { ...message('old-assistant', 'answer'), role: 'assistant' },
        ]
          .map((row) => JSON.stringify(row))
          .join('\n') + '\n',
      ),
    )
    const result = await fetchSessionMessages(
      'hosted-session',
      {
        commit_count: 1,
        uri: 'viking://user/wulv/sessions/hosted-session/',
      } as Parameters<typeof fetchSessionMessages>[1],
      'content',
    )
    expect(result.map(({ id }) => id)).toEqual(['old-user', 'old-assistant'])
    expect(getSessionIdArchiveByArchiveIdMock).not.toHaveBeenCalled()
    expect(getContentReadMock).toHaveBeenCalledWith({
      query: {
        uri: 'viking://user/wulv/sessions/hosted-session/history/archive_001/messages.jsonl',
        offset: 0,
        limit: -1,
        raw: true,
      },
    })
  })

  it('merges hosted archives in order and deduplicates retained current messages', async () => {
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('2', 'two'), message('3', 'three')] }),
    )
    getContentReadMock
      .mockReturnValueOnce(response(JSON.stringify(message('1', 'one'))))
      .mockReturnValueOnce(response(JSON.stringify(message('2', 'two'))))
    const result = await fetchSessionMessages(
      's',
      { commit_count: 2, uri: 'viking://user/u/sessions/s' } as Parameters<
        typeof fetchSessionMessages
      >[1],
      'content',
    )
    expect(result.map(({ id }) => id)).toEqual(['1', '2', '3'])
  })

  it('reports malformed archive lines instead of silently showing an empty session', async () => {
    getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
    getContentReadMock.mockReturnValue(response('{invalid json}'))
    await expect(
      fetchSessionMessages(
        's',
        { commit_count: 1, uri: 'viking://user/u/sessions/s' } as Parameters<
          typeof fetchSessionMessages
        >[1],
        'content',
      ),
    ).rejects.toThrow('archive_001')
  })

  it.each([
    response({ content: 'unexpected envelope' }),
    response(JSON.stringify({ ...message('bad', 'text'), parts: [null] })),
  ])(
    'rejects invalid archive shapes with a useful archive location',
    async (archive) => {
      getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
      getContentReadMock.mockReturnValue(archive)
      await expect(
        fetchSessionMessages(
          's',
          {
            commit_count: 1,
            uri: 'viking://user/u/sessions/s',
          } as Parameters<typeof fetchSessionMessages>[1],
          'content',
        ),
      ).rejects.toThrow('archive_001')
    },
  )

  it('preserves access failures instead of treating protected history as empty', async () => {
    getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
    getContentReadMock.mockRejectedValue(
      new OvClientError({
        code: 'FORBIDDEN',
        statusCode: 403,
        message: 'History access denied',
      }),
    )
    await expect(
      fetchSessionMessages(
        's',
        {
          commit_count: 1,
          uri: 'viking://user/u/sessions/s',
        } as Parameters<typeof fetchSessionMessages>[1],
        'content',
      ),
    ).rejects.toThrow('History access denied')
  })

  it.each(['api', 'content'] as const)(
    'validates the same message contract for %s archives',
    async (source) => {
      const invalid = {
        ...message('bad', 'text'),
        parts: [{ type: 'text', text: 123 }],
      }
      getSessionIdContextMock.mockReturnValue(response({ messages: [] }))
      getSessionIdArchiveByArchiveIdMock.mockReturnValue(
        response({ messages: [invalid] }),
      )
      getContentReadMock.mockReturnValue(response(JSON.stringify(invalid)))
      await expect(
        fetchSessionMessages(
          's',
          {
            commit_count: 1,
            uri: 'viking://user/u/sessions/s',
          } as Parameters<typeof fetchSessionMessages>[1],
          source,
        ),
      ).rejects.toThrow('invalid message')
    },
  )

  it('reports malformed current messages rather than silently dropping them', async () => {
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [{ id: 'bad', role: 'user', parts: null }] }),
    )
    await expect(fetchSessionMessages('s', undefined, 'none')).rejects.toThrow(
      'invalid message',
    )
  })

  it('keeps readable history when an unfinished archive is unavailable', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 2 }))
    getSessionIdArchiveByArchiveIdMock
      .mockRejectedValueOnce(
        new OvClientError({
          code: 'NOT_FOUND',
          message: 'archive not found',
          statusCode: 404,
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

    expect(result.map(({ id }) => id)).toEqual(['2', '3'])
  })

  it('propagates archive failures that are not missing archives', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 1 }))
    getSessionIdArchiveByArchiveIdMock.mockRejectedValueOnce(
      new Error('connection reset'),
    )
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('1', 'one')] }),
    )

    await expect(fetchSessionMessages('session-1')).rejects.toThrow(
      'connection reset',
    )
  })
})

describe('serializeParts', () => {
  it('keeps persisted content order while filtering timeline-only events', () => {
    expect(
      serializeParts([
        { type: 'iteration', iteration: 1 },
        { type: 'reasoning', reasoning: 'thinking' },
        {
          type: 'tool',
          tool_id: 'tool-1',
          tool_name: 'search',
          tool_uri: '',
          skill_uri: '',
          tool_status: 'completed',
          tool_output: 'result',
        },
        {
          type: 'tool_result',
          tool_id: 'tool-1',
          tool_name: 'search',
          tool_output: 'result',
          is_error: false,
        },
        { type: 'text', text: 'done' },
      ]),
    ).toEqual([
      expect.objectContaining({
        type: 'tool',
        tool_id: 'tool-1',
        tool_output: 'result',
      }),
      { type: 'text', text: 'done' },
    ])
  })
})

describe('fetchSessionFirstTitle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('stops at the first archived user message without reading later history', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 20 }))
    getSessionIdArchiveByArchiveIdMock.mockReturnValue(
      response({ messages: [message('first', 'First\n question')] }),
    )
    expect(await fetchSessionFirstTitle('s')).toBe('First question')
    expect(getSessionIdArchiveByArchiveIdMock).toHaveBeenCalledExactlyOnceWith({
      path: { session_id: 's', archive_id: 'archive_001' },
    })
    expect(getSessionIdContextMock).not.toHaveBeenCalled()
  })

  it('reads current context when there are no archives', async () => {
    getSessionBySessionIdMock.mockReturnValue(response({ commit_count: 0 }))
    getSessionIdContextMock.mockReturnValue(
      response({ messages: [message('1', 'Hello')] }),
    )
    expect(await fetchSessionFirstTitle('s')).toBe('Hello')
    expect(getSessionIdArchiveByArchiveIdMock).not.toHaveBeenCalled()
  })

  it('limits simultaneous title lookups to four and releases slots after errors', async () => {
    let active = 0
    let peak = 0
    getSessionBySessionIdMock.mockImplementation(async () => {
      active += 1
      peak = Math.max(peak, active)
      await new Promise((resolve) => setTimeout(resolve, 5))
      active -= 1
      throw new Error('unavailable')
    })
    const results = await Promise.allSettled(
      Array.from({ length: 12 }, (_, i) => fetchSessionFirstTitle(String(i))),
    )
    expect(peak).toBe(4)
    expect(results.every((result) => result.status === 'rejected')).toBe(true)
    expect(getSessionBySessionIdMock).toHaveBeenCalledTimes(12)
  })
})

describe('Bot fetch credentials', () => {
  afterEach(() => {
    ovClient.clearConnection()
    vi.unstubAllGlobals()
  })

  it('does not fall back to a Root key for user Bot requests in api_key mode', async () => {
    ovClient.setOptions({ baseUrl: 'http://localhost:1933' })
    ovClient.setConnection({
      accountId: 'account-1',
      userId: 'user-1',
      apiKey: '',
      adminApiKey: 'root-key',
      identityHeaders: false,
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    await fetchBotHealth()
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty('X-API-Key')
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty(
      'X-OpenViking-User',
    )
  })

  it.each([
    { apiKey: '', expectedKey: 'root-key' },
    { apiKey: 'data-key', expectedKey: 'data-key' },
  ])('uses $expectedKey for Bot requests', async ({ apiKey, expectedKey }) => {
    ovClient.setOptions({ baseUrl: 'http://localhost:1933' })
    ovClient.setConnection({
      accountId: 'account-1',
      adminApiKey: 'root-key',
      apiKey,
      identityHeaders: true,
      userId: 'user-1',
    })
    const fetchMock = vi.fn().mockImplementation(async (url: string) =>
      url.endsWith('/stream')
        ? new Response('data: hello\n\n', {
            headers: { 'Content-Type': 'text/event-stream' },
          })
        : new Response(JSON.stringify({ accepted: true, interrupted: true })),
    )
    vi.stubGlobal('fetch', fetchMock)

    await fetchBotHealth()
    const messages = []
    for await (const event of await sendChatStream({ message: 'hello' })) {
      messages.push(event.data)
    }
    expect(messages).toEqual(['hello'])
    await steerChatSession('session 1', { message: 'next input' })
    await interruptChatSession('session 1')

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      'http://localhost:1933/bot/v1/health',
      'http://localhost:1933/bot/v1/chat/stream',
      'http://localhost:1933/bot/v1/sessions/session%201/steer',
      'http://localhost:1933/bot/v1/sessions/session%201/interrupt',
    ])
    for (const [, options] of fetchMock.mock.calls) {
      expect(options.headers).toMatchObject({
        'X-API-Key': expectedKey,
        'X-OpenViking-Account': 'account-1',
        'X-OpenViking-User': 'user-1',
      })
    }
  })
})

describe('interruptChatSession', () => {
  beforeEach(() => {
    ovClient.setOptions({ baseUrl: 'http://localhost:1933' })
    ovClient.setConnection({
      accountId: 'account-1',
      apiKey: 'api-key-1',
      identityHeaders: true,
      userId: 'user-1',
    })
  })

  it('posts to the scoped bot interrupt endpoint with auth headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ interrupted: true }), {
        headers: { 'Content-Type': 'application/json' },
        status: 200,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(interruptChatSession('session 1')).resolves.toEqual({
      interrupted: true,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:1933/bot/v1/sessions/session%201/interrupt',
      {
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': 'api-key-1',
          'X-OpenViking-Account': 'account-1',
          'X-OpenViking-User': 'user-1',
        },
        method: 'POST',
      },
    )
  })
})

describe('steerChatSession', () => {
  beforeEach(() => {
    ovClient.setOptions({ baseUrl: 'http://localhost:1933' })
    ovClient.setConnection({
      accountId: 'account-1',
      apiKey: 'api-key-1',
      identityHeaders: true,
      userId: 'user-1',
    })
  })

  it('posts the message to the scoped bot steer endpoint with auth headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accepted: true }), {
        headers: { 'Content-Type': 'application/json' },
        status: 200,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      steerChatSession('session 1', { message: 'next input' }),
    ).resolves.toEqual({ accepted: true })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:1933/bot/v1/sessions/session%201/steer',
      {
        body: JSON.stringify({
          message: 'next input',
          session_id: 'session 1',
          user_id: 'user-1',
        }),
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': 'api-key-1',
          'X-OpenViking-Account': 'account-1',
          'X-OpenViking-User': 'user-1',
        },
        method: 'POST',
      },
    )
  })
})
