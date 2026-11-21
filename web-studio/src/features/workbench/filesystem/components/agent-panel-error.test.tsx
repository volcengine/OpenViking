// @vitest-environment jsdom
import { AgentPanel } from '#/features/workbench/filesystem/components/agent-panel'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const m = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock(
  '#/features/workbench/sessions/lib/use-default-conversation-titles',
  () => ({ useDefaultConversationTitles: vi.fn() }),
)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { error?: string; returnObjects?: boolean }) =>
      options?.returnObjects
        ? []
        : options?.error
          ? `${key}: ${options.error}`
          : key,
  }),
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
  useBotHealth: () => ({ isLoading: false, isError: false, refetch: vi.fn() }),
  useDeleteSession: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
  }),
  useCreateSession: () => ({ mutateAsync: m.create }),
  useSessionListByRecency: () => ({ data: [], isLoading: false }),
  useSessionMessages: () => ({ data: undefined }),
}))

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  window.localStorage.clear()
  m.create.mockReset().mockImplementation(async (id: string) => ({
    session_id: id,
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

async function sendWithResponse(response: Response) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
  const toolbar = document.createElement('div')
  document.body.append(toolbar)
  render(
    <AgentPanel
      toolbarContainer={toolbar}
      onSessionChange={vi.fn()}
      onOpenResource={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hi' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'chat.send' }))
  })
  return screen.findByRole('alert')
}

it('shows the parsed message when the chat request is rejected', async () => {
  const body = {
    status: 'error',
    error: { code: 'UNAUTHENTICATED', message: 'Missing API Key.' },
  }
  const alert = await sendWithResponse(
    new Response(JSON.stringify(body), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    }),
  )
  expect(alert.textContent).toContain('Missing API Key.')
  expect(alert.textContent).not.toContain('UNAUTHENTICATED')
})

it('shows an error event received from the chat stream', async () => {
  const event = {
    event: 'error',
    data: JSON.stringify({ error: 'Bot service unavailable: boom' }),
  }
  const alert = await sendWithResponse(
    new Response(`data: ${JSON.stringify(event)}\n\n`, {
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
    }),
  )
  expect(alert.textContent).toContain('Bot service unavailable: boom')
  expect(alert.textContent).not.toContain('{')
})
