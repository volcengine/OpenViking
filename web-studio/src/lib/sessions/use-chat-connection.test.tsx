// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useChat } from './use-chat'
import { ovClient } from '#/lib/ov-client'

const mocks = vi.hoisted(() => ({ stream: vi.fn(), postMessage: vi.fn() }))
vi.mock('./api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  sendChatStream: mocks.stream,
}))
vi.mock('#/gen/ov-client/sdk.gen', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  postSessionIdMessages: mocks.postMessage,
}))
vi.mock('./use-session-titles', () => ({ setSessionTitle: vi.fn() }))
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
  mocks.postMessage.mockImplementation(async () => {
    writes.push(ovClient.getOptions().baseUrl)
    return { data: { status: 'ok', result: {} } }
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
  mocks.postMessage.mockImplementation(async () => {
    writes.push(ovClient.getOptions().baseUrl)
    ovClient.setOptions({ baseUrl: 'http://other.example' })
    return { data: { status: 'ok', result: {} } }
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
