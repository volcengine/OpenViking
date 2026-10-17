// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useChat } from './use-chat'
import { ovClient } from '#/lib/ov-client'

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  addMessage: vi.fn(),
}))
vi.mock('./api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  addMessage: mocks.addMessage,
  sendChatStream: mocks.stream,
}))
vi.mock('./use-session-titles', () => ({ setSessionTitle: vi.fn() }))

function sse(event: string, data: unknown) {
  return { data: JSON.stringify({ event, data }) }
}

afterEach(() => {
  vi.clearAllMocks()
})

it('surfaces provider_recovery as transient status and keeps it out of the transcript', async () => {
  let release!: () => void
  const paused = new Promise<void>((resolve) => {
    release = resolve
  })
  mocks.stream.mockImplementation(async () =>
    (async function* () {
      yield sse('provider_recovery', {
        reason: 'primary_failed',
        error_kind: 'rate_limit_error',
      })
      await paused
      yield sse('content_delta', 'backup says hi')
      yield sse('response', { content: 'backup says hi' })
    })(),
  )
  mocks.addMessage.mockResolvedValue({})

  const view = renderHook(() =>
    useChat({ sessionId: 'recovery-session', identityScopeKey: 'scope' }),
  )
  let sending!: Promise<void>
  await act(async () => {
    sending = view.result.current.send('hello')
  })

  await waitFor(() => {
    expect(view.result.current.providerRecovery).not.toBeNull()
  })
  expect(view.result.current.providerRecovery).toEqual({
    reason: 'primary_failed',
    error_kind: 'rate_limit_error',
  })
  // Not assistant content while active.
  expect(view.result.current.streamingContent).toBe('')

  await act(async () => {
    release()
    await sending
  })

  // Cleared once real output/final response arrives.
  expect(view.result.current.providerRecovery).toBeNull()
  // The durable transcript holds only the real assistant text.
  const assistant = view.result.current.messages.find(
    (message) => message.role === 'assistant',
  )
  expect(assistant).toBeDefined()
  const text = JSON.stringify(assistant?.parts)
  expect(text).toContain('backup says hi')
  expect(text).not.toContain('primary_failed')
  expect(text).not.toContain('provider_recovery')
})
