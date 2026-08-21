// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'

import { fetchSse, SseResponseError } from './sse'

function sseResponse(chunks: Array<string>): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } },
  )
}

describe('fetchSse', () => {
  it('supports POST and parses standard SSE event boundaries', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse([
          ': heartbeat\r\n',
          'id: event-1\r\nevent: update\r\ndata: first\r\n',
          'data: second\r\n\r\n',
        ]),
      )

    const messages = []
    for await (const message of fetchSse('/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"message":"hello"}',
      fetch: fetchMock,
    })) {
      messages.push(message)
    }

    expect(fetchMock).toHaveBeenCalledWith(
      '/stream',
      expect.objectContaining({
        body: '{"message":"hello"}',
        method: 'POST',
      }),
    )
    expect(messages).toEqual([
      {
        data: 'first\nsecond',
        event: 'update',
        id: 'event-1',
        retry: undefined,
      },
    ])
  })

  it('rejects non-SSE responses without retrying the POST', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('invalid request', {
        status: 400,
        headers: { 'Content-Type': 'text/plain' },
      }),
    )

    const consume = async () => {
      for await (const _message of fetchSse('/stream', {
        method: 'POST',
        fetch: fetchMock,
      })) {
        // No messages expected.
      }
    }

    await expect(consume()).rejects.toBeInstanceOf(SseResponseError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('aborts an active POST stream', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new ReadableStream<Uint8Array>(), {
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )

    const consume = async () => {
      for await (const _message of fetchSse('/stream', {
        method: 'POST',
        fetch: fetchMock,
        signal: controller.signal,
      })) {
        // No messages expected.
      }
    }
    const consuming = consume()
    await Promise.resolve()
    controller.abort()

    await expect(consuming).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not start a POST when the signal is already aborted', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn()
    controller.abort()

    const consume = async () => {
      for await (const _message of fetchSse('/stream', {
        method: 'POST',
        fetch: fetchMock,
        signal: controller.signal,
      })) {
        // No messages expected.
      }
    }

    await expect(consume()).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
