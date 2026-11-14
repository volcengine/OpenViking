// @vitest-environment jsdom
import { Thread } from '#/features/workbench/sessions/components/thread'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  provider: 'opensource',
  request: vi.fn<typeof fetch>(),
  history: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: mocks.provider, ready: true }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({ getTitle: (id: string) => id }),
}))
vi.mock(
  '#/features/workbench/sessions/lib/use-default-conversation-titles',
  () => ({
    useDefaultConversationTitles: () => {},
  }),
)
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useSession: () => ({}),
  useSessionMessages: mocks.history,
}))
vi.mock('#/features/workbench/sessions/components/memory-impact', () => ({
  MemoryImpact: () => null,
}))
vi.mock('#/shared/api/module-request', () => ({
  requestStudioModule: mocks.request,
}))
vi.mock('#/shared/ui/pixel-blast', () => ({ default: () => null }))

const sessionUri = 'viking://session/draft'
const ok = (result: unknown) => Response.json({ status: 'ok', result })
const requestPath = (input: Parameters<typeof fetch>[0]) =>
  new URL(String(input), window.location.origin).pathname
const callsTo = (pathname: string, method = 'GET') =>
  mocks.request.mock.calls.filter(
    ([input, init]) =>
      requestPath(input) === pathname && (init?.method ?? 'GET') === method,
  )

function renderThread(thread: ReactNode) {
  const client = new QueryClient()
  return render(thread, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
}

beforeEach(() => {
  mocks.provider = 'opensource'
  mocks.history.mockReset().mockReturnValue({ isPending: true })
  mocks.request.mockReset().mockImplementation(async (input) => {
    const pathname = requestPath(input)
    if (pathname === '/api/v1/content/read') return ok('')
    return ok({ session_id: 'draft', uri: sessionUri, commit_count: 0 })
  })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(cleanup)

it('loads existing sessions without creating or listing sessions in Console', async () => {
  const view = renderThread(<Thread sessionId="first" />)
  await waitFor(() => expect(callsTo('/api/v1/sessions/first')).toHaveLength(1))
  view.rerender(<Thread sessionId="second" />)
  await waitFor(() =>
    expect(callsTo('/api/v1/sessions/second')).toHaveLength(1),
  )
  expect(callsTo('/api/v1/sessions')).toHaveLength(0)
  expect(callsTo('/api/v1/sessions', 'POST')).toHaveLength(0)
})

it('keeps hosted session history read-only', () => {
  mocks.provider = 'volcengine'
  mocks.history.mockReturnValue({ isPending: false, data: [] })
  renderThread(<Thread sessionId="hosted-session" />)
  expect(mocks.history).toHaveBeenLastCalledWith('hosted-session')
  expect(screen.getByText('chat.emptyReadDescription')).toBeTruthy()
  expect(document.querySelector('.ov-viking-bot__composer')).toBeNull()
  expect(mocks.request).not.toHaveBeenCalled()
})

it('does not request server history or create a session when opening a draft', () => {
  renderThread(<Thread sessionId="draft" draft />)
  expect(mocks.history).toHaveBeenLastCalledWith(undefined)
  expect(mocks.request).not.toHaveBeenCalled()
})
