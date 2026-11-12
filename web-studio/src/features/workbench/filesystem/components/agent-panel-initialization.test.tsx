// @vitest-environment jsdom
import { AgentPanel } from './agent-panel'
import type { VikingBotProps } from '@openviking/react/components/viking-bot'
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

let conversation: Partial<VikingBotProps>
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock(
  '#/features/workbench/sessions/lib/use-default-conversation-titles',
  () => ({ useDefaultConversationTitles: vi.fn() }),
)
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    removeTitle: vi.fn(),
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useBotHealth: () => ({ isLoading: false }),
  useSessionListByRecency: () => ({ data: [], isLoading: false }),
}))
const child = (value: Partial<VikingBotProps>) => {
  conversation = value
  return null
}
beforeEach(() => window.localStorage.clear())
afterEach(cleanup)
it('leaves chat ownership and the full editor to Console', () => {
  render(<AgentPanel onSessionChange={vi.fn()}>{child}</AgentPanel>)
  expect(conversation.messages).toBeUndefined()
  expect(conversation.onSend).toBeUndefined()
  expect(conversation.composer).toBeUndefined()
})
it('reflects a server-created ID and external route selection without creating a chat controller', () => {
  const changed = vi.fn()
  const view = render(
    <AgentPanel onSessionChange={changed}>{child}</AgentPanel>,
  )
  act(() => conversation.sessions!.onSelect('server-created'))
  expect(conversation.sessions!.currentId).toBe('server-created')
  expect(changed).toHaveBeenLastCalledWith('server-created')
  view.rerender(
    <AgentPanel initialSessionId="external" onSessionChange={changed}>
      {child}
    </AgentPanel>,
  )
  expect(conversation.sessions!.currentId).toBe('external')
  act(() => conversation.sessions!.onNew())
  expect(conversation.sessions!.currentId).toBeUndefined()
  expect(changed).toHaveBeenLastCalledWith('')
})
