// @vitest-environment jsdom
import { useChat } from '#/features/workbench/sessions/lib/use-chat'
import { ovClient } from '#/shared/api/ov-client/index'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  steer: vi.fn(),
  addMessage: vi.fn(),
  interrupt: vi.fn().mockResolvedValue({ interrupted: false }),
}))
vi.mock('#/features/workbench/sessions/lib/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  addMessage: mocks.addMessage,
  interruptChatSession: mocks.interrupt,
  sendChatStream: mocks.stream,
  steerChatSession: mocks.steer,
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  setSessionTitle: vi.fn(),
}))
const originalOptions = ovClient.getOptions()
afterEach(() => {
  ovClient.setOptions(originalOptions)
  vi.clearAllMocks()
})

it('does not persist a completed stream after its owner unmounts and the connection changes', async () => {
  let finish!: () => void
  const pause = new Promise<void>((resolve) => {
    finish = resolve
  })
  let signal!: AbortSignal
  mocks.stream.mockImplementation(async (_request, abortSignal) => {
    signal = abortSignal
    return (async function* () {
      await pause
      if (signal.aborted) return
      yield {
        data: JSON.stringify({
          event: 'response',
          data: { content: 'synthetic source response' },
        }),
      }
    })()
  })
  const writes: string[] = []
  mocks.addMessage.mockImplementation(async () => {
    writes.push(ovClient.getOptions().baseUrl)
    return {}
  })
  ovClient.setOptions({ baseUrl: 'http://source.example' })
  const view = renderHook(() =>
    useChat({ sessionId: 'same-session-id', identityScopeKey: 'source-scope' }),
  )
  let sending!: Promise<void>
  await act(async () => {
    sending = view.result.current.send('synthetic source message')
  })
  view.unmount()
  ovClient.setOptions({ baseUrl: 'http://other.example' })
  await act(async () => {
    finish()
    await sending
  })
  expect(writes).toEqual([])
})

it('does not send the assistant message to a changed connection after the user write finishes', async () => {
  mocks.stream.mockResolvedValue(
    (async function* () {
      yield {
        data: JSON.stringify({
          event: 'response',
          data: { content: 'synthetic response' },
        }),
      }
    })(),
  )
  const writes: string[] = []
  mocks.addMessage.mockImplementation(async () => {
    writes.push(ovClient.getOptions().baseUrl)
    ovClient.setOptions({ baseUrl: 'http://other.example' })
    return {}
  })
  ovClient.setOptions({ baseUrl: 'http://source.example' })
  const view = renderHook(() =>
    useChat({ sessionId: 'same-session-id', identityScopeKey: 'source-scope' }),
  )
  await act(async () => {
    await view.result.current.send('synthetic message')
  })
  expect(writes).toEqual(['http://source.example'])
  view.unmount()
})

it('does not interrupt a changed connection', async () => {
  let finish!: () => void
  const pause = new Promise<void>((resolve) => {
    finish = resolve
  })
  mocks.stream.mockResolvedValue(
    (async function* () {
      await pause
      yield* []
    })(),
  )
  mocks.interrupt.mockResolvedValue({ interrupted: false })
  ovClient.setOptions({ baseUrl: 'http://source.example' })
  const view = renderHook(() =>
    useChat({ sessionId: 'same-session-id', identityScopeKey: 'source-scope' }),
  )

  let sending!: Promise<void>
  await act(async () => {
    sending = view.result.current.send('synthetic source message')
  })
  ovClient.setOptions({ baseUrl: 'http://other.example' })
  act(() => {
    view.result.current.abort()
  })
  finish()
  await act(async () => {
    await sending
  })

  expect(mocks.interrupt).not.toHaveBeenCalled()
  view.unmount()
})

it('rejects steer input after the active stream connection changes', async () => {
  let finish!: () => void
  const pause = new Promise<void>((resolve) => {
    finish = resolve
  })
  mocks.stream.mockResolvedValue(
    (async function* () {
      await pause
      yield* []
    })(),
  )
  mocks.steer.mockResolvedValue({ accepted: true })
  ovClient.setOptions({ baseUrl: 'http://source.example' })
  const view = renderHook(() =>
    useChat({ sessionId: 'same-session-id', identityScopeKey: 'source-scope' }),
  )

  let sending!: Promise<void>
  await act(async () => {
    sending = view.result.current.send('synthetic source message')
  })
  await waitFor(() => expect(view.result.current.status).toBe('streaming'))

  ovClient.setOptions({ baseUrl: 'http://other.example' })
  await expect(view.result.current.send('synthetic follow-up')).rejects.toThrow(
    'Active request belongs to a different connection',
  )

  expect(mocks.steer).not.toHaveBeenCalled()
  expect(view.result.current.messages).toHaveLength(1)

  finish()
  await act(async () => {
    await sending
  })
  view.unmount()
})

it('stops persisting the timeline when the connection changes between writes', async () => {
  let finishStream!: () => void
  const streamPaused = new Promise<void>((resolve) => {
    finishStream = resolve
  })
  let finishAssistantWrite!: () => void
  const assistantWritePaused = new Promise<void>((resolve) => {
    finishAssistantWrite = resolve
  })
  let assistantWriteStarted!: () => void
  const assistantWritePending = new Promise<void>((resolve) => {
    assistantWriteStarted = resolve
  })
  mocks.stream.mockResolvedValue(
    (async function* () {
      yield {
        data: JSON.stringify({
          event: 'content_delta',
          data: 'first segment',
        }),
      }
      await streamPaused
      yield {
        data: JSON.stringify({
          event: 'iteration',
          data: 'Iteration 2/10',
        }),
      }
      yield {
        data: JSON.stringify({
          event: 'response',
          data: 'final segment',
        }),
      }
    })(),
  )
  mocks.steer.mockResolvedValue({ accepted: true })
  const writes: string[] = []
  mocks.addMessage.mockImplementation(async () => {
    writes.push(ovClient.getOptions().baseUrl)
    if (writes.length === 2) {
      assistantWriteStarted()
      await assistantWritePaused
    }
    return {}
  })
  ovClient.setOptions({ baseUrl: 'http://source.example' })
  const view = renderHook(() =>
    useChat({ sessionId: 'same-session-id', identityScopeKey: 'source-scope' }),
  )

  let sending!: Promise<void>
  await act(async () => {
    sending = view.result.current.send('initial message')
  })
  await waitFor(() =>
    expect(view.result.current.streamingContent).toBe('first segment'),
  )
  await act(async () => {
    await view.result.current.send('first follow up')
  })
  await waitFor(() => expect(writes).toHaveLength(1))
  finishStream()
  await assistantWritePending
  ovClient.setOptions({ baseUrl: 'http://other.example' })
  finishAssistantWrite()
  await act(async () => {
    await sending
  })

  expect(writes).toEqual(['http://source.example', 'http://source.example'])
  view.unmount()
})
