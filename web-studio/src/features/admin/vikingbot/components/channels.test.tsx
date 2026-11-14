// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Channels } from './channels'

const api = vi.hoisted(() => ({
  getConnections: vi.fn(),
  updateConnection: vi.fn(),
}))
vi.mock('#/features/admin/vikingbot/api', () => api)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      values?.user ? `User: ${values.user}` : key,
  }),
}))
vi.mock('#/features/admin/users/components/user-space-switcher', () => ({
  UserSpaceSwitcher: () => <button>space</button>,
}))
vi.mock('#/features/admin/vikingbot/components/delete-connection', () => ({
  DeleteConnection: () => <button>delete</button>,
}))
vi.mock('#/features/admin/vikingbot/providers/registry', () => {
  const provider = {
    label: 'feishu',
    addLabel: 'addFeishu',
    Setup: () => <p>setup editor</p>,
    Settings: () => <button>settings</button>,
  }
  return {
    providers: { feishu: provider },
    upcomingProviders: [],
    getProvider: () => provider,
  }
})
const clients: QueryClient[] = []
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <Channels canManage scope="space-one" />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  api.getConnections.mockResolvedValue([
    {
      id: 'one',
      bot_name: 'Research bot',
      identity_user: 'Lily',
      enabled: true,
      step: 5,
      status: { state: 'connected' },
    },
    {
      id: 'two',
      bot_name: 'Writing bot',
      identity_user: 'Carol',
      enabled: false,
      step: 5,
      status: { state: 'paused' },
    },
  ])
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})
it('filters real bots by name and linked user without invented statistics', async () => {
  mount()
  expect(
    await screen.findByRole('button', { name: /Research bot/ }),
  ).toBeTruthy()
  expect(screen.getAllByTitle('statisticsUnavailable')).toHaveLength(2)
  fireEvent.change(screen.getByRole('textbox', { name: 'searchBots' }), {
    target: { value: 'carol' },
  })
  expect(screen.queryByRole('button', { name: /Research bot/ })).toBeNull()
  expect(screen.getByRole('button', { name: /Writing bot/ })).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'searchBots' }), {
    target: { value: 'missing' },
  })
  expect(screen.getByText('noMatchingBots')).toBeTruthy()
})
it('opens the existing setup from a bot card', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Research bot/ }))
  expect(screen.getByText('setup editor')).toBeTruthy()
  expect(api.updateConnection).not.toHaveBeenCalled()
})
it('keeps existing management actions available in the card disclosure', async () => {
  mount()
  await screen.findByRole('button', { name: /Research bot/ })
  const disclosure = screen.getAllByLabelText('botActions')[0]
  fireEvent.click(disclosure)
  expect(disclosure.closest('details')?.open).toBe(true)
  expect(screen.getAllByRole('button', { name: 'pause' })).toHaveLength(1)
  expect(screen.getByText('state.paused')).toBeTruthy()
})
