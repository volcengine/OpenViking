// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchSse } from '#/lib/sse'
import { useChat } from './use-chat'

const {
  addMessageMock,
  interruptChatSessionMock,
  sendChatStreamMock,
  steerChatSessionMock,
} = vi.hoisted(() => ({
  addMessageMock: vi.fn(),
  interruptChatSessionMock: vi.fn(),
  sendChatStreamMock: vi.fn(),
  steerChatSessionMock: vi.fn(),
}))

vi.mock('./api', () => ({
  addMessage: addMessageMock,
  interruptChatSession: interruptChatSessionMock,
  sendChatStream: sendChatStreamMock,
  steerChatSession: steerChatSessionMock,
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

function textMessage(id: string, role: 'user' | 'assistant', text: string) {
  return {
    id,
    role,
    parts: [{ type: 'text' as const, text }],
    created_at: '2026-01-01T00:00:00.000Z',
  }
}

describe('useChat event timeline', () => {
  beforeEach(() => {
    addMessageMock.mockReset()
    addMessageMock.mockResolvedValue({})
    interruptChatSessionMock.mockReset()
    interruptChatSessionMock.mockResolvedValue({ interrupted: false })
    sendChatStreamMock.mockReset()
    steerChatSessionMock.mockReset()
    steerChatSessionMock.mockResolvedValue({ accepted: true })
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

  it('starts a new request after a terminal response while persistence is pending', async () => {
    let releasePersistence!: () => void
    addMessageMock
      .mockResolvedValueOnce({})
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releasePersistence = () => resolve({})
          }),
      )
      .mockResolvedValue({})
    sendChatStreamMock
      .mockResolvedValueOnce(
        streamMessages([{ event: 'response', data: 'first answer' }]),
      )
      .mockResolvedValueOnce(
        streamMessages([{ event: 'response', data: 'second answer' }]),
      )

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: true,
        sessionId: 'session-1',
      }),
    )

    let firstSend!: Promise<void>
    await act(async () => {
      firstSend = result.current.send('first question')
    })
    await waitFor(() => expect(result.current.status).toBe('idle'))

    let secondSend!: Promise<void>
    await act(async () => {
      secondSend = result.current.send('second question')
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(steerChatSessionMock).not.toHaveBeenCalled()
    expect(sendChatStreamMock).toHaveBeenCalledTimes(2)
    expect(addMessageMock.mock.calls.map((call) => call[2])).toEqual([
      'first question',
      undefined,
    ])

    releasePersistence()
    await Promise.all([firstSend, secondSend])
    expect(addMessageMock.mock.calls.map((call) => call[2])).toEqual([
      'first question',
      undefined,
      'second question',
      undefined,
    ])
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

  it('requests a backend interrupt before falling back to local abort', async () => {
    sendChatStreamMock.mockImplementation((_request, signal: AbortSignal) =>
      fetchSse('/stream', {
        signal,
        fetch: vi.fn().mockResolvedValue(
          new Response(new ReadableStream<Uint8Array>(), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
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
      await new Promise((resolve) => setTimeout(resolve, 0))
      result.current.abort()
      await sendPromise
    })

    expect(interruptChatSessionMock).toHaveBeenCalledWith('session-1')
    expect(result.current.status).toBe('idle')
  })

  it('stops streaming and persists only the user message when interrupted', async () => {
    let streamReleased = false
    async function* stream() {
      try {
        yield {
          data: JSON.stringify({
            event: 'interrupt',
            data: { reason: 'user' },
          }),
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
        persistMessages: true,
        sessionId: 'session-1',
      }),
    )

    await act(async () => {
      await result.current.send('hello')
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.streamingParts).toEqual([])
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0]?.role).toBe('user')
    expect(addMessageMock).toHaveBeenCalledTimes(1)
    expect(addMessageMock).toHaveBeenCalledWith('session-1', 'user', 'hello')
    expect(streamReleased).toBe(true)
  })

  it('keeps accepted steer inputs visible when an acknowledged interrupt closes the stream', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const encoder = new TextEncoder()
    interruptChatSessionMock.mockResolvedValueOnce({ interrupted: true })
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                streamController = controller
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
      sendPromise = result.current.send('A')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))

    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'content_delta',
            data: 'partial',
          })}\n\n`,
        ),
      )
      await result.current.send('B')
      await result.current.send('D')
    })

    await act(async () => {
      result.current.abort()
      await new Promise((resolve) => setTimeout(resolve, 0))
      streamController.close()
      await sendPromise
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.streamingInsertIndex).toBeUndefined()
    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['A', 'B', 'D'])
  })

  it('removes committed assistant segments when an acknowledged interrupt closes the stream', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const encoder = new TextEncoder()
    interruptChatSessionMock.mockResolvedValueOnce({ interrupted: true })
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                streamController = controller
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
        persistMessages: true,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('A')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))

    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'content_delta',
            data: 'partial',
          })}\n\n`,
        ),
      )
      await result.current.send('B')
      await result.current.send('D')
    })

    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'iteration',
            data: 'Iteration 2/10',
          })}\n\n`,
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['A', 'partial', 'B', 'D'])

    await act(async () => {
      result.current.abort()
      await new Promise((resolve) => setTimeout(resolve, 0))
      streamController.close()
      await sendPromise
    })

    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['A', 'B', 'D'])
    expect(addMessageMock.mock.calls.map((call) => call[1])).toEqual([
      'user',
      'user',
      'user',
    ])
    expect(addMessageMock.mock.calls.map((call) => call[2])).toEqual([
      'A',
      'B',
      'D',
    ])
  })

  it('does not hide local steer inputs when a stale history snapshot arrives after interrupt', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    interruptChatSessionMock.mockResolvedValueOnce({ interrupted: true })
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                streamController = controller
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
      }),
    )

    const { result, rerender } = renderHook(
      ({ initialMessages }) =>
        useChat({
          identityScopeKey: 'identity',
          initialMessages,
          persistMessages: false,
          sessionId: 'session-1',
        }),
      { initialProps: { initialMessages: [] } },
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('A')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))

    await act(async () => {
      await result.current.send('B')
      await result.current.send('D')
      result.current.abort()
      await new Promise((resolve) => setTimeout(resolve, 0))
      streamController.close()
      await sendPromise
    })

    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['A', 'B', 'D'])

    rerender({
      initialMessages: [
        textMessage('history-a', 'user', 'A'),
        textMessage('history-b', 'user', 'B'),
      ],
    })

    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['A', 'B', 'D'])
  })

  it('merges late initial history with local messages sent before history loads', async () => {
    sendChatStreamMock.mockResolvedValue(
      streamMessages([{ event: 'response', data: 'new answer' }]),
    )

    const { result, rerender } = renderHook(
      ({ initialMessages }) =>
        useChat({
          identityScopeKey: 'identity',
          initialMessages,
          persistMessages: false,
          sessionId: 'session-1',
        }),
      { initialProps: { initialMessages: undefined as Message[] | undefined } },
    )

    await act(async () => {
      await result.current.send('new question')
    })
    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['new question', 'new answer'])

    rerender({
      initialMessages: [
        textMessage('history-a', 'user', 'old question'),
        textMessage('history-b', 'assistant', 'old answer'),
      ],
    })

    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['old question', 'old answer', 'new question', 'new answer'])
  })

  it('keeps the steer boundary when the next iteration arrives before its ACK', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    let acceptSteer!: (result: { accepted: boolean }) => void
    const encoder = new TextEncoder()
    steerChatSessionMock.mockReturnValueOnce(
      new Promise((resolve) => {
        acceptSteer = resolve
      }),
    )
    sendChatStreamMock.mockResolvedValue(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                streamController = controller
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
        persistMessages: true,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    let steerPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('hello')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))
    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'content_delta',
            data: 'before steer',
          })}\n\n`,
        ),
      )
    })
    await waitFor(() =>
      expect(result.current.streamingContent).toBe('before steer'),
    )
    await act(async () => {
      steerPromise = result.current.send('follow up')
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(addMessageMock.mock.calls.map((call) => call[1])).toEqual(['user'])

    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          [
            { event: 'iteration', data: 'Iteration 2/10' },
            { event: 'content_delta', data: 'after steer' },
          ]
            .map((event) => `data: ${JSON.stringify(event)}\n\n`)
            .join(''),
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    await act(async () => {
      acceptSteer({ accepted: true })
      await steerPromise
    })

    await waitFor(() =>
      expect(
        result.current.messages.slice(1).map((message) => message.parts),
      ).toEqual([
        [{ type: 'text', text: 'before steer' }],
        [{ type: 'text', text: 'follow up' }],
      ]),
    )
    expect(result.current.streamingContent).toBe('after steer')

    await act(async () => {
      streamController.enqueue(
        encoder.encode(
          [{ event: 'response', data: 'answer' }]
            .map((event) => `data: ${JSON.stringify(event)}\n\n`)
            .join(''),
        ),
      )
      streamController.close()
      await sendPromise
    })

    expect(sendChatStreamMock).toHaveBeenCalledTimes(1)
    expect(steerChatSessionMock).toHaveBeenCalledWith('session-1', {
      message: 'follow up',
    })
    expect(addMessageMock.mock.calls.map((call) => call[1])).toEqual([
      'user',
      'assistant',
      'user',
      'assistant',
    ])
    expect(
      result.current.messages.slice(1).map((message) => message.parts),
    ).toEqual([
      [{ type: 'text', text: 'before steer' }],
      [{ type: 'text', text: 'follow up' }],
      [
        { type: 'iteration', iteration: 2 },
        { type: 'text', text: 'answer' },
      ],
    ])
  })

  it('keeps rejected steer input out of the timeline', async () => {
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
    steerChatSessionMock.mockRejectedValueOnce(new Error('turn closed'))

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    await act(async () => {
      void result.current.send('hello')
      controller.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'content_delta',
            data: 'still streaming',
          })}\n\n`,
        ),
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    await act(async () => {
      await expect(result.current.send('follow up')).rejects.toThrow(
        'turn closed',
      )
    })

    expect(result.current.messages.map((message) => message.role)).toEqual([
      'user',
    ])
    expect(result.current.streamingContent).toBe('still streaming')
    expect(result.current.streamingParts).toEqual([
      { type: 'text', text: 'still streaming' },
    ])
    expect(result.current.error).toBe('turn closed')

    await act(async () => {
      controller.close()
    })
  })

  it('ignores an aborted stream after switching sessions', async () => {
    let rejectOldStream!: () => void
    let newController!: ReadableStreamDefaultController<Uint8Array>
    let acceptSteer!: (result: { accepted: boolean }) => void
    const encoder = new TextEncoder()
    steerChatSessionMock.mockReturnValueOnce(
      new Promise((resolve) => {
        acceptSteer = resolve
      }),
    )
    async function* oldStream() {
      await new Promise<void>((resolve) => {
        rejectOldStream = resolve
      })
      yield* []
    }
    sendChatStreamMock.mockResolvedValueOnce(oldStream()).mockResolvedValueOnce(
      fetchSse('/stream', {
        fetch: vi.fn().mockResolvedValue(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                newController = controller
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
      }),
    )

    const { result, rerender } = renderHook(
      ({ sessionId }) =>
        useChat({
          identityScopeKey: 'identity',
          persistMessages: true,
          sessionId,
        }),
      { initialProps: { sessionId: 'session-1' } },
    )

    let oldSend!: Promise<void>
    let steerSend!: Promise<void>
    await act(async () => {
      oldSend = result.current.send('old request')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))
    await act(async () => {
      steerSend = result.current.send('follow up')
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    rerender({ sessionId: 'session-2' })
    let newSend!: Promise<void>
    await act(async () => {
      newSend = result.current.send('new request')
      newController.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({
            event: 'content_delta',
            data: 'new answer',
          })}\n\n`,
        ),
      )
    })
    await waitFor(() =>
      expect(result.current.streamingContent).toBe('new answer'),
    )

    await act(async () => {
      acceptSteer({ accepted: true })
      rejectOldStream()
      await steerSend
      await oldSend
    })

    expect(result.current.status).toBe('streaming')
    expect(result.current.streamingContent).toBe('new answer')
    expect(result.current.error).toBeUndefined()
    expect(addMessageMock).toHaveBeenCalledWith(
      'session-1',
      'user',
      'follow up',
    )

    await act(async () => {
      newController.close()
      await newSend
    })
  })

  it('ignores an aborted stream after resetting the same session', async () => {
    let releaseStream!: () => void
    async function* stream() {
      await new Promise<void>((resolve) => {
        releaseStream = resolve
      })
      yield* []
    }
    sendChatStreamMock.mockResolvedValue(stream())

    const { result } = renderHook(() =>
      useChat({
        identityScopeKey: 'identity',
        persistMessages: false,
        sessionId: 'session-1',
      }),
    )

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.send('first request')
    })
    await waitFor(() => expect(result.current.status).toBe('streaming'))
    await act(async () => {
      await result.current.send('follow up')
    })
    expect(
      result.current.messages.map(
        (message) => message.parts[0]?.type === 'text' && message.parts[0].text,
      ),
    ).toEqual(['first request', 'follow up'])

    await act(async () => {
      result.current.reset()
      releaseStream()
      await sendPromise
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.messages).toEqual([])
    expect(result.current.streamingInsertIndex).toBeUndefined()
  })

  it('drops the unfinished assistant segment when aborted before content arrives', async () => {
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
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0]?.parts).toEqual([
      { type: 'text', text: 'hello' },
    ])
  })
})
