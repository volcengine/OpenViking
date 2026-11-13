// @vitest-environment jsdom
import { Thread } from './thread'
import { createThreadRequest } from './thread-request'
import { cleanup, render, screen } from '@testing-library/react'
import type { ConsoleProps } from '@openviking/react/modules/console'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const m = vi.hoisted(() => ({
  provider: 'opensource',
  request: vi.fn(),
  history: vi.fn(),
  console: undefined as ConsoleProps | undefined,
  invalidate: vi.fn(),
  setTitle: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: m.invalidate }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: m.provider, ready: true }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    setTitle: m.setTitle,
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useSession: () => ({}),
  useSessionMessages: m.history,
}))
vi.mock('#/shared/api/module-request', () => ({
  requestStudioModule: m.request,
}))
vi.mock('@openviking/react/client', async (original) => ({
  ...(await original<object>()),
  OpenVikingProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@openviking/react/modules/console', () => ({
  Console: (props: ConsoleProps) => {
    m.console = props
    return (
      <>
        {props.conversation?.header}
        {props.conversation?.emptyState}
      </>
    )
  },
}))
vi.mock('./memory-impact', () => ({ MemoryImpact: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  m.provider = 'opensource'
  m.history.mockReturnValue({ isPending: false, data: [] })
  m.request.mockResolvedValue(Response.json({ status: 'ok', result: {} }))
})
afterEach(cleanup)

it('uses conversation-only Console, skips draft history and echoes the created ID without remounting', () => {
  const persisted = vi.fn()
  const view = render(
    <Thread sessionId="draft" draft onPersisted={persisted} />,
  )
  expect(m.console?.modes).toEqual(['conversation'])
  expect(m.console?.conversation?.messages).toBeUndefined()
  expect(m.console?.conversation?.onSend).toBeUndefined()
  expect(m.console?.conversation?.sessions?.currentId).toBeUndefined()
  expect(m.history).toHaveBeenLastCalledWith(undefined)
  view.rerender(<Thread sessionId="existing" />)
  expect(m.console?.conversation?.sessions?.currentId).toBe('existing')
  expect(persisted).not.toHaveBeenCalled()
})

it('preserves hosted history as read-only content', () => {
  m.provider = 'volcengine'
  render(<Thread sessionId="hosted" />)
  expect(m.history).toHaveBeenLastCalledWith('hosted')
  expect(m.console?.conversation?.messages).toEqual([])
  expect(m.console?.conversation?.composer).toBe(false)
  expect(screen.getByText('chat.emptyReadDescription')).toBeTruthy()
})

it('preserves the Studio session ID and request cancellation signal', async () => {
  const request = createThreadRequest({
    sessionId: 'vikingbot-web-test',
    onMessageSaved: vi.fn(),
  })
  const signal = new AbortController().signal
  await request('/api/v1/sessions', { method: 'POST', body: '{}', signal })
  expect(m.request).toHaveBeenCalledWith('/api/v1/sessions', {
    method: 'POST',
    body: JSON.stringify({ session_id: 'vikingbot-web-test' }),
    signal,
  })
})

it('updates product metadata only after successful saves and retains the first title', async () => {
  const saved = vi.fn()
  const request = createThreadRequest({ sessionId: 's', onMessageSaved: saved })
  const send = (text: string) =>
    request('/api/v1/sessions/s/messages', {
      method: 'POST',
      body: JSON.stringify({ role: 'user', parts: [{ type: 'text', text }] }),
    })
  m.request.mockResolvedValueOnce(Response.json({ status: 'error' }))
  await send('failed')
  expect(saved).not.toHaveBeenCalled()
  await send('first')
  await send('second')
  expect(saved.mock.calls).toEqual([['first'], [undefined]])
})

it('does not overwrite an existing session title on follow-up', async () => {
  const saved = vi.fn()
  const request = createThreadRequest({
    sessionId: 's',
    hasTitle: true,
    onMessageSaved: saved,
  })
  await request('/api/v1/sessions/s/messages', {
    method: 'POST',
    body: JSON.stringify({
      role: 'user',
      parts: [{ type: 'text', text: 'follow-up' }],
    }),
  })
  expect(saved).toHaveBeenCalledWith(undefined)
})
