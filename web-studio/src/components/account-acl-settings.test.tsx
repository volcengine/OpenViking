// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AccountAclSettings } from './account-acl-settings'

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  enabled: false,
  allowed: true,
}))
vi.mock('#/hooks/use-acl-management', () => ({
  useAclManagement: () => ({
    allowed: mocks.allowed,
    api: { setEnabled: mocks.update },
    settingsKey: ['account-acl', 'acme'],
    connection: { accountId: 'acme' },
    settings: { data: mocks.enabled, isSuccess: true, isFetching: false },
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
beforeEach(() => {
  vi.resetAllMocks()
  mocks.enabled = false
  mocks.allowed = true
  mocks.update.mockResolvedValue(true)
})
afterEach(cleanup)
function mount() {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountAclSettings />
    </QueryClientProvider>,
  )
  return userEvent.setup()
}
it('requires confirmation before enabling account ACL', async () => {
  const user = mount()
  await user.click(screen.getByRole('button', { name: 'acl.enableAction' }))
  expect(screen.getByText('acl.enableWarning')).toBeTruthy()
  expect(mocks.update).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'acl.confirm' }))
  await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(true))
})
it('explains expanded access on disable and cancellation leaves settings unchanged', async () => {
  mocks.enabled = true
  const user = mount()
  await user.click(screen.getByRole('button', { name: 'acl.disableAction' }))
  expect(screen.getByText('acl.disableWarning')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'actions.cancel' }))
  expect(mocks.update).not.toHaveBeenCalled()
})
it('does not show account controls to non-administrators', () => {
  mocks.allowed = false
  mount()
  expect(screen.queryByRole('button', { name: 'acl.enableAction' })).toBeNull()
})
it('opens advanced controls on hover and keeps confirmation available after leaving', async () => {
  mocks.enabled = true
  const client = new QueryClient()
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={client}>
      <AccountAclSettings inPopover />
    </QueryClientProvider>,
  )
  const trigger = screen.getByRole('button', { name: 'acl.page.advanced' })
  await user.hover(trigger)
  await user.click(
    await screen.findByRole('button', { name: 'acl.disableAction' }),
  )
  await user.unhover(trigger)
  expect(screen.getByRole('button', { name: 'acl.confirm' })).toBeTruthy()
})
