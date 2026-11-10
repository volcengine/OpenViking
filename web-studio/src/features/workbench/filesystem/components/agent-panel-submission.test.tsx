// @vitest-environment jsdom
import { AgentPanel } from '#/features/workbench/filesystem/components/agent-panel'
import { registerFilesystemAgentSessionId } from '#/features/workbench/filesystem/lib/utils'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const m = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
  stream: vi.fn(),
  history: vi.fn(),
}))
vi.mock(
  '#/features/workbench/sessions/lib/use-default-conversation-titles',
  () => ({
    useDefaultConversationTitles: vi.fn(),
  }),
)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    setTitle: vi.fn(),
    removeTitle: vi.fn(),
  }),
  setSessionTitle: vi.fn(),
}))
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useBotHealth: () => ({ isLoading: true }),
  useDeleteSession: () => ({
    mutateAsync: m.remove,
    reset: vi.fn(),
    isPending: false,
  }),
  useCreateSession: () => ({ mutateAsync: m.create }),
  useSessionListByRecency: () => ({
    data: [{ session_id: 'B' }],
    isLoading: false,
  }),
  useSessionMessages: m.history,
}))
vi.mock('#/features/workbench/sessions/lib/api', () => ({
  sendChatStream: m.stream,
  addMessage: vi.fn(),
  serializeParts: vi.fn(),
  interruptChatSession: vi.fn().mockResolvedValue({ interrupted: false }),
}))
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  window.localStorage.clear()
  m.remove.mockReset().mockResolvedValue(undefined)
  m.create.mockReset()
  m.history.mockReset().mockReturnValue({ data: undefined })
  m.stream.mockReset().mockImplementation(() => new Promise(() => {}))
})
afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})
function submit(text: string) {
  fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'chat.send' }))
}
function setup() {
  let resolve!: (value: { session_id: string }) => void
  let reject!: (error: Error) => void
  m.create.mockImplementation(
    () =>
      new Promise((res, rej) => {
        resolve = res
        reject = rej
      }),
  )
  registerFilesystemAgentSessionId('B', 'test')
  const toolbar = document.createElement('div')
  document.body.append(toolbar)
  const url = vi.fn()
  render(
    <AgentPanel
      toolbarContainer={toolbar}
      onSessionChange={url}
      onOpenResource={vi.fn()}
    />,
  )
  return {
    url,
    resolve: (id: string) => resolve({ session_id: id }),
    reject: (error: Error) => reject(error),
  }
}
it('preserves edits and blocks duplicate submission while creation waits', async () => {
  const pending = setup()
  submit('first')
  const id = m.create.mock.calls[0][0]
  submit('second')
  expect(screen.getByRole('textbox').value).toBe('second')
  expect(m.stream).not.toHaveBeenCalled()
  await act(async () => {
    pending.resolve(id)
  })
  expect(m.stream).toHaveBeenCalledTimes(1)
  expect(m.stream.mock.calls[0][0]).toEqual({
    message: 'first',
    session_id: id,
  })
})
it('ignores a late creation after switching to another session', async () => {
  const pending = setup()
  submit('message for A')
  const id = m.create.mock.calls[0][0]
  fireEvent.click(screen.getByTitle('agent.history'))
  fireEvent.click(screen.getAllByText('B')[0].closest('button')!)
  expect(pending.url).toHaveBeenLastCalledWith('B')
  expect(m.history).toHaveBeenLastCalledWith('B')
  await act(async () => {
    pending.resolve(id)
  })
  expect(pending.url).toHaveBeenLastCalledWith('B')
  expect(m.history).toHaveBeenLastCalledWith('B')
  expect(m.stream).not.toHaveBeenCalled()
})
it('preserves the first input when creation fails', async () => {
  const pending = setup()
  submit('important input')
  await act(async () => {
    pending.reject(new Error('offline'))
  })
  expect(screen.getByRole('textbox').value).toBe('important input')
  expect(m.stream).not.toHaveBeenCalled()
})

it('clears accepted input once creation succeeds', async () => {
  const pending = setup()
  submit('first')
  expect(screen.getByRole('textbox').value).toBe('first')
  const id = m.create.mock.calls[0][0]
  await act(async () => {
    pending.resolve(id)
  })
  expect(screen.getByRole('textbox').value).toBe('')
  expect(m.stream).toHaveBeenCalledTimes(1)
})

it('does not send or publish after the panel unmounts', async () => {
  const pending = setup()
  submit('first')
  const id = m.create.mock.calls[0][0]
  cleanup()
  await act(async () => {
    pending.resolve(id)
  })
  expect(pending.url).not.toHaveBeenCalled()
  expect(m.stream).not.toHaveBeenCalled()
})

it('deletes the active history session only after confirmation and returns to a draft', async () => {
  const pending = setup()
  fireEvent.click(screen.getByRole('button', { name: 'agent.history' }))
  fireEvent.click(screen.getByRole('button', { name: 'BB' }))
  fireEvent.click(screen.getByRole('button', { name: 'agent.history' }))
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.deleteSession' }),
  )
  expect(m.remove).not.toHaveBeenCalled()
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'threadList.confirmDelete' }),
    )
  })
  expect(m.remove).toHaveBeenCalledWith('B')
  expect(pending.url).toHaveBeenLastCalledWith('')
  expect(m.create).not.toHaveBeenCalled()
})

it('shows new session only for an existing conversation and opens an unpersisted draft', () => {
  const pending = setup()
  expect(screen.queryByTitle('agent.newSession')).toBeNull()
  fireEvent.click(screen.getByTitle('agent.history'))
  fireEvent.click(screen.getAllByText('B')[0].closest('button')!)
  expect(screen.getByTitle('agent.newSession')).toBeTruthy()
  fireEvent.click(screen.getByTitle('agent.newSession'))
  expect(m.create).not.toHaveBeenCalled()
  expect(m.history).toHaveBeenLastCalledWith(undefined)
  expect(pending.url).toHaveBeenCalledWith('')
  expect(screen.queryByTitle('agent.newSession')).toBeNull()
})
