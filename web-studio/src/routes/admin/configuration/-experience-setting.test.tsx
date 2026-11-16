// @vitest-environment jsdom
import { EvolutionSettingRow } from '#/features/admin/account-configuration/experience-setting'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  mode: 'management',
  role: 'admin',
  read: vi.fn(),
  write: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { accountId: 'team-a' },
    connectionRole: state.role,
    identityScopeKey: 'team-a-admin',
    isConnectionRoleLoading: false,
    studioMode: state.mode,
  }),
}))
vi.mock('#/features/admin/account-configuration/experience-api', () => ({
  fetchAgentEvolutionStatus: state.read,
  setAgentEvolutionEnabled: state.write,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const clients: QueryClient[] = []
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(<EvolutionSettingRow />, {
    wrapper: ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
}
beforeEach(() => {
  vi.clearAllMocks()
  state.mode = 'management'
  state.role = 'admin'
  state.read.mockResolvedValue({ accountId: 'team-a', enabled: true })
  state.write.mockResolvedValue({ accountId: 'team-a', enabled: false })
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})
it('edits the current Account inline without a second popover or page', async () => {
  mount()
  await screen.findByText('settings.statusEnabled')
  const control = screen.getByRole('switch', { name: 'settings.toggleLabel' })
  expect(control.getAttribute('aria-checked')).toBe('true')
  fireEvent.click(control)
  await waitFor(() => expect(state.write).toHaveBeenCalledWith('team-a', false))
  await screen.findByText('settings.statusDisabled')
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('blocks toggling when the response belongs to another Account', async () => {
  state.read.mockResolvedValue({ accountId: 'team-b', enabled: true })
  mount()
  expect(await screen.findByRole('alert')).toHaveProperty(
    'textContent',
    'settings.scopeMismatch',
  )
  const control = screen.getByRole('switch', { name: 'settings.toggleLabel' })
  expect(control.hasAttribute('data-disabled')).toBe(true)
  fireEvent.click(control)
  expect(state.write).not.toHaveBeenCalled()
})
it('shows a retry action after a read failure and preserves the current value after a write failure', async () => {
  state.read.mockRejectedValueOnce(new Error('offline'))
  state.write.mockRejectedValue(new Error('not saved'))
  mount()
  expect(await screen.findByRole('alert')).toHaveProperty(
    'textContent',
    'settings.loadFailed',
  )
  fireEvent.click(screen.getByRole('button', { name: 'refresh' }))
  await screen.findByText('settings.statusEnabled')
  fireEvent.click(screen.getByRole('switch', { name: 'settings.toggleLabel' }))
  await waitFor(() => expect(state.write).toHaveBeenCalled())
  expect(
    screen
      .getByRole('switch', { name: 'settings.toggleLabel' })
      .getAttribute('aria-checked'),
  ).toBe('true')
})
it.each([
  ['workbench', 'root'],
  ['management', 'user'],
])(
  'does not request account settings in %s mode with %s role',
  (mode, role) => {
    state.mode = mode
    state.role = role
    mount()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(state.read).not.toHaveBeenCalled()
  },
)
