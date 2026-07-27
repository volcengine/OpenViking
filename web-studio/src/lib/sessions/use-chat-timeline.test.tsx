// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchSse } from '#/lib/sse'
import { useChat } from './use-chat'

const { sendChatStreamMock } = vi.hoisted(() => ({
  sendChatStreamMock: vi.fn(),
}))

vi.mock('./api', () => ({
  addMessage: vi.fn(),
  sendChatStream: sendChatStreamMock,
  serializeParts: vi.fn(),
}))

vi.mock('./use-session-titles', () => ({
  setSessionTitle: vi.fn(),
}))

vi.mock('../browser-crypto', () => ({
  createBrowserId: vi.fn((prefix: string) => `${prefix}-id`),
}))

function streamResponse(
  events: Array<{ event: string; data: unknown }>,
): Response {
  const payload = events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join('')
  return new Response(payload, {
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function streamMessages(events: Array<{ event: string; data: unknown }>) {
  return fetchSse('/stream', {
    fetch: vi.fn().mockResolvedValue(streamResponse(events)),
  })
}

describe('useChat event timeline', () => {
  beforeEach(() => {
    sendChatStreamMock.mockReset()
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  it('keeps grouped event blocks in SSE arrival order', async () => {
    sendChatStreamMock.mockResolvedValue(
      streamMessages([
        { event: 'iteration', data: 'Iteration 1/10' },
        { event: 'reasoning_delta', data: 'first reasoning' },
        { event: 'content_delta', data: 'checking' },
        { event: 'tool_call', data: 'search({})' },
        { event: 'tool_result', data: '{"ok":true}' },
        { event: 'iteration', data: 'Iteration 2/10' },
        { event: 'reasoning', data: 'second reasoning' },
        { event: 'content_delta', data: 'final' },
        { event: 'response', data: { content: 'final' } },
      ]),
    )

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    await act(async () => {
      await result.current.send('hello')
    })

    expect(result.current.messages[1]?.parts.map((part) => part.type)).toEqual([
      'iteration',
      'reasoning',
      'text',
      'tool',
      'tool_result',
      'iteration',
      'reasoning',
      'text',
    ])
    expect(result.current.messages[1]?.parts[3]).toMatchObject({
      tool_name: 'search',
      tool_output: '{"ok":true}',
      tool_status: 'completed',
    })
    expect(result.current.messages[1]?.parts[4]).toMatchObject({
      tool_name: 'search',
      tool_output: '{"ok":true}',
      type: 'tool_result',
    })
    expect(
      result.current.messages[1]?.parts
        .filter((part) => part.type === 'reasoning')
        .map((part) => part.is_running),
    ).toEqual([false, false])
  })

  it('keeps only the latest reasoning block running', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const encoder = new TextEncoder()
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(streamController) {
                controller = streamController
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
      }),
    )

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('hello')
      controller.enqueue(
        encoder.encode(
          [
            { event: 'reasoning_delta', data: 'first' },
            { event: 'tool_call', data: 'search({})' },
            { event: 'tool_result', data: '{}' },
            { event: 'reasoning_delta', data: 'second' },
          ]
            .map((event) => `data: ${JSON.stringify(event)}\n\n`)
            .join(''),
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(
      result.current.streamingParts
        .filter((part) => part.type === 'reasoning')
        .map((part) => part.is_running),
    ).toEqual([false, true])

    await act(async () => {
      controller.close()
      await sendPromise
    })
  })

  it('renders a complete reasoning event as completed', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const encoder = new TextEncoder()
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(streamController) {
                controller = streamController
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
      }),
    )

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('hello')
      controller.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'reasoning',
            data: 'complete reasoning',
          })}\n\n`,
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(result.current.streamingParts).toContainEqual({
      type: 'reasoning',
      reasoning: 'complete reasoning',
      is_running: false,
    })

    await act(async () => {
      controller.close()
      await sendPromise
    })
  })

  it('finalizes when the response event arrives before the stream closes', async () => {
    let streamReleased = false
    async function* stream() {
      try {
        yield {
          data: JSON.stringify({ event: 'response', data: 'final response' }),
          event: '',
          id: '',
        }
        await new Promise(() => {})
      } finally {
        streamReleased = true
      }
    }
    sendChatStreamMock.mockResolvedValue(stream())

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    await act(async () => {
      await result.current.send('hello')
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.messages[1]?.parts).toContainEqual({
      text: 'final response',
      type: 'text',
    })
    expect(streamReleased).toBe(true)
  })

  it('keeps reasoning and tool timeline when aborted before content arrives', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const encoder = new TextEncoder()
    sendChatStreamMock.mockImplementation((_request, signal: AbortSignal) =>
      fetchSse('/stream', {
        signal,
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(streamController) {
                controller = streamController
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
      }),
    )

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('hello')
      controller.enqueue(
        encoder.encode(
          [
            { event: 'reasoning_delta', data: 'checking' },
            { event: 'tool_call', data: 'search({})' },
          ]
            .map((event) => `data: ${JSON.stringify(event)}\n\n`)
            .join(''),
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
      result.current.abort()
      await sendPromise
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.streamingParts).toEqual([])
    expect(result.current.messages[1]?.parts).toMatchObject([
      {
        is_running: false,
        reasoning: 'checking',
        type: 'reasoning',
      },
      {
        tool_name: 'search',
        tool_status: 'running',
        type: 'tool',
      },
    ])
  })
})
