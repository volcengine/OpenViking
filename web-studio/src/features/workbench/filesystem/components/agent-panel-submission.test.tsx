import type { VikingBotComposerProps } from '@openviking/react/components/viking-bot'
// @vitest-environment jsdom
import { AgentPanel as AgentConversation } from '#/features/workbench/filesystem/components/agent-panel'
import { registerFilesystemAgentSessionId } from '#/features/workbench/filesystem/lib/utils'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Console } from '@openviking/react/modules/console'
import type { ComponentProps } from 'react'

function AgentPanel(
  props: Omit<ComponentProps<typeof AgentConversation>, 'children'>,
) {
  return (
    <AgentConversation {...props}>
      {(conversation) => (
        <Console mode="conversation" conversation={conversation} />
      )}
    </AgentConversation>
  )
}

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
vi.mock('@openviking/react/components/viking-bot', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  VikingBotComposer: ({
    value,
    onValueChange,
    onSend,
    disabled,
    labels,
  }: VikingBotComposerProps) => (
    <div>
      <input
        value={value}
        onChange={(event) => onValueChange?.(event.target.value)}
      />
      <button
        disabled={disabled}
        onClick={() => onSend?.({ text: value, attachments: [] })}
      >
        {labels?.send}
      </button>
    </div>
  ),
}))
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  window.localStorage.clear()
  m.remove.mockReset().mockResolvedValue(undefined)
  m.create.mockReset()
  m.history.mockReset().mockReturnValue({ data: undefined })
  m.stream.mockReset().mockResolvedValue(true)
})
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
)
afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})
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
  const url = vi.fn()
  render(<AgentPanel onSessionChange={url} />)
  return {
    url,
    resolve: (id: string) => resolve({ session_id: id }),
    reject: (error: Error) => reject(error),
  }
}
it('deletes the active history session only after confirmation and returns to a draft', async () => {
  const pending = setup()
  fireEvent.click(screen.getByRole('combobox', { name: 'agent.history' }))
  fireEvent.click(screen.getAllByText('B')[0].closest('button')!)
  fireEvent.click(screen.getByLabelText('threadList.deleteSession'))
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

it('uses the Console new-session action to open an unpersisted draft', () => {
  const pending = setup()
  expect(screen.getByRole('button', { name: 'agent.newSession' })).toBeTruthy()
  fireEvent.click(screen.getByRole('combobox', { name: 'agent.history' }))
  fireEvent.click(screen.getAllByText('B')[0].closest('button')!)
  expect(screen.getByRole('button', { name: 'agent.newSession' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'agent.newSession' }))
  expect(m.create).not.toHaveBeenCalled()
  expect(pending.url).toHaveBeenCalledWith('')
  expect(screen.getByRole('button', { name: 'agent.newSession' })).toBeTruthy()
})
