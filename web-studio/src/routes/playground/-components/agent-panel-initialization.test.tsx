// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AgentPanel } from './agent-panel'

const mocks = vi.hoisted(() => ({
  history: vi.fn(),
  create: vi.fn(),
  send: vi.fn(),
  onSend: undefined as undefined | ((message: string) => Promise<void>),
}))
vi.mock('#/lib/sessions/use-default-conversation-titles', () => ({
  useDefaultConversationTitles: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/lib/sessions/use-session-titles', () => ({
  useSessionTitles: () => ({ getTitle: () => '', setTitle: vi.fn() }),
}))
vi.mock('#/lib/sessions/use-sessions', () => ({
  useBotHealth: () => ({ isLoading: true }),
  useCreateSession: () => ({ mutateAsync: mocks.create }),
  useSessionListByRecency: () => ({ data: [], isLoading: false }),
  useSessionMessages: mocks.history,
}))
vi.mock('#/lib/sessions/use-chat', () => ({
  useChat: () => ({
    messages: [],
    status: 'idle',
    abort: vi.fn(),
    setMessages: vi.fn(),
    send: mocks.send,
  }),
}))

vi.mock('#/routes/sessions/-components/composer', () => ({
  Composer: ({ onSend }: { onSend: (message: string) => Promise<void> }) => {
    mocks.onSend = onSend
    return null
  },
}))

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  mocks.history.mockReset().mockReturnValue({ data: undefined })
  mocks.create.mockReset()
  mocks.send.mockReset()
  window.localStorage.clear()
})
afterEach(cleanup)

it('keeps a new draft out of history requests and the URL', () => {
  const onSessionChange = vi.fn()
  render(
    <AgentPanel
      onOpenResource={vi.fn()}
      onSessionChange={onSessionChange}
      toolbarContainer={null}
    />,
  )
  expect(mocks.history).toHaveBeenCalledWith(undefined)
  expect(onSessionChange).not.toHaveBeenCalled()
  expect(mocks.create).not.toHaveBeenCalled()
})

it('loads history for an existing session', () => {
  render(
    <AgentPanel
      initialSessionId="existing"
      onOpenResource={vi.fn()}
      onSessionChange={vi.fn()}
      toolbarContainer={null}
    />,
  )
  expect(mocks.history).toHaveBeenCalledWith('existing')
})

it('creates the draft before sending and publishes only the persisted ID', async () => {
  const onSessionChange = vi.fn()
  let resolveCreation!: (value: { session_id: string }) => void
  mocks.create.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveCreation = resolve
      }),
  )
  render(
    <AgentPanel
      onOpenResource={vi.fn()}
      onSessionChange={onSessionChange}
      toolbarContainer={null}
    />,
  )
  let sending!: Promise<void>
  act(() => {
    sending = mocks.onSend!('hello')
  })
  expect(mocks.send).not.toHaveBeenCalled()
  expect(onSessionChange).not.toHaveBeenCalled()
  const id = mocks.create.mock.calls[0][0]
  expect(id).toMatch(/^vikingbot-web-/)
  await act(async () => {
    resolveCreation({ session_id: id })
    await sending
  })
  expect(onSessionChange).toHaveBeenCalledWith(id)
  expect(mocks.send).toHaveBeenCalledWith('hello')
  expect(
    mocks.history.mock.calls.every(([historyId]) => historyId === undefined),
  ).toBe(true)
})

it('does not send or publish the draft when creation fails', async () => {
  const onSessionChange = vi.fn()
  mocks.create.mockRejectedValue(new Error('unavailable'))
  render(
    <AgentPanel
      onOpenResource={vi.fn()}
      onSessionChange={onSessionChange}
      toolbarContainer={null}
    />,
  )
  await act(async () => {
    await mocks.onSend!('hello')
  })
  expect(mocks.send).not.toHaveBeenCalled()
  expect(onSessionChange).not.toHaveBeenCalled()
})
