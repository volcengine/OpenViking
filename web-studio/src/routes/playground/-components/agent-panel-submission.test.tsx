// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AgentPanel } from './agent-panel'
import { registerPlaygroundAgentSessionId } from '../-lib/utils'

const m = vi.hoisted(() => ({
  create: vi.fn(),
  stream: vi.fn(),
  history: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/lib/sessions/use-session-titles', () => ({
  useSessionTitles: () => ({ getTitle: (id: string) => id, setTitle: vi.fn() }),
  setSessionTitle: vi.fn(),
}))
vi.mock('#/lib/sessions/use-sessions', () => ({
  useBotHealth: () => ({ isLoading: true }),
  useCreateSession: () => ({ mutateAsync: m.create }),
  useSessionListByRecency: () => ({
    data: [{ session_id: 'B' }],
    isLoading: false,
  }),
  useSessionMessages: m.history,
}))
vi.mock('#/lib/sessions/api', () => ({
  sendChatStream: m.stream,
  addMessage: vi.fn(),
  serializeParts: vi.fn(),
}))
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  window.localStorage.clear()
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
  registerPlaygroundAgentSessionId('B', 'test')
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
