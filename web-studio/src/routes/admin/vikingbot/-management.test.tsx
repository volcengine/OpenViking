// @vitest-environment jsdom
import { BotManagementPage } from '#/features/admin/vikingbot/management-page'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  role: 'root',
  capabilities: vi.fn(),
  connections: vi.fn(),
  histories: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'account-one',
    connectionRole: state.role,
  }),
}))
vi.mock('#/features/admin/vikingbot/api', () => ({
  getCapabilities: state.capabilities,
  getConnections: state.connections,
  getConversations: state.histories,
}))
vi.mock('#/features/admin/vikingbot/components/channels', () => ({
  Channels: () => <p>channel editor</p>,
}))
vi.mock('#/features/admin/vikingbot/components/platform-history', () => ({
  PlatformHistory: () => <p>platform messages</p>,
}))
const clients: QueryClient[] = []
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <BotManagementPage />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  state.role = 'root'
  state.capabilities.mockResolvedValue({ enabled: true, can_manage: true })
  state.connections.mockResolvedValue([{ id: 'feishu-one', type: 'feishu' }])
  state.histories.mockResolvedValue([
    { conversation: 'group-one', title: 'Group thread', time: '2026-10-30' },
  ])
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})
it.each(['user', 'admin'])(
  'never loads the Root management API for %s',
  (role) => {
    state.role = role
    mount()
    expect(screen.getByText('modes.rootRequired')).toBeTruthy()
    expect(state.capabilities).not.toHaveBeenCalled()
    expect(state.connections).not.toHaveBeenCalled()
  },
)
it('provides channels and platform history only to Root, without web-chat controls', async () => {
  mount()
  expect(await screen.findByText('channel editor')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'newChat' })).toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: 'conversations' }))
  fireEvent.click(await screen.findByRole('button', { name: /Group thread/ }))
  expect(await screen.findByText('platform messages')).toBeTruthy()
})
it('does not load channels while the Bot gateway refuses management', async () => {
  state.capabilities.mockResolvedValue({ enabled: true, can_manage: false })
  mount()
  expect(await screen.findByText('adminOnly')).toBeTruthy()
  expect(screen.queryByText('channel editor')).toBeNull()
  expect(state.connections).not.toHaveBeenCalled()
})
