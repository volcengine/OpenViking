// @vitest-environment jsdom
import { VikingBotPage } from '#/features/workbench/vikingbot/workbench-page'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const admin = vi.hoisted(() => ({
  capabilities: vi.fn(),
  connections: vi.fn(),
  histories: vi.fn(),
}))
vi.mock('#/features/admin/vikingbot/api', () => ({
  getCapabilities: admin.capabilities,
  getConnections: admin.connections,
  getConversations: admin.histories,
}))
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'root-and-user',
    connection: { apiKey: 'user-key', adminApiKey: 'root-key' },
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useBotHealth: () => ({ isError: false, isPending: false }),
  useSessionListByRecency: () => ({
    data: [
      {
        session_id: 'vikingbot-web-63507b10-0000-4000-8000-000000000001',
        mod_time: '2026-10-30',
      },
      { session_id: 'external-group', mod_time: '2026-10-30' },
    ],
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    removeTitle: vi.fn(),
  }),
}))
vi.mock(
  '#/features/workbench/sessions/lib/use-default-conversation-titles',
  () => ({
    useDefaultConversationTitles: vi.fn(),
  }),
)
vi.mock('#/features/workbench/sessions/components/delete-conversation', () => ({
  DeleteConversation: () => null,
}))
vi.mock('#/features/workbench/sessions/components/thread', () => ({
  Thread: ({ sessionId }: { sessionId: string }) => (
    <p data-testid="thread">{sessionId}</p>
  ),
}))
afterEach(cleanup)
it('keeps web chat usable without mounting management APIs even when Root is saved', () => {
  render(<VikingBotPage />)
  expect(screen.queryByRole('tab', { name: 'channels' })).toBeNull()
  expect(screen.queryByText('manageBots')).toBeNull()
  expect(screen.queryByText('external-group')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'newChat' }))
  expect(screen.getByTestId('thread').textContent).toMatch(/^vikingbot-web-/)
  expect(admin.capabilities).not.toHaveBeenCalled()
  expect(admin.connections).not.toHaveBeenCalled()
  expect(admin.histories).not.toHaveBeenCalled()
})
