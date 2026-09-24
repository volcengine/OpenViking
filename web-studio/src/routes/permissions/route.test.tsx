// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { OvClientError } from '#/lib/ov-client'
import { PermissionsPage } from './-components/permissions-page'

const root = 'viking://resources/'
const mocks = vi.hoisted(() => ({
  accountId: 'acme',
  allowed: true,
  enabled: true,
  modes: {} as Record<string, 'none' | 'inherit' | 'restricted'>,
  list: vi.fn(),
  get: vi.fn(),
}))
vi.mock('#/hooks/use-acl-management', () => ({
  useAclManagement: () => ({
    allowed: mocks.allowed,
    settings: { data: mocks.enabled, isSuccess: true, isFetching: false },
    settingsKey: ['account-acl', mocks.accountId],
    connection: { baseUrl: 'http://localhost', accountId: mocks.accountId },
    aclIdentityScopeKey: mocks.accountId,
    api: { get: mocks.get, listDirectory: mocks.list },
  }),
}))
vi.mock('#/components/account-acl-settings', () => ({
  AccountAclSettings: ({ inPopover }: { inPopover?: boolean }) => (
    <div data-testid={inPopover ? 'advanced-settings' : 'account-settings'} />
  ),
}))
vi.mock('#/components/resource-permissions', () => ({
  ResourcePermissionsPanel: ({ uri }: { uri: string }) => (
    <div data-testid="editor">{uri}</div>
  ),
}))
vi.mock('#/components/resource-acl-identity-recovery', () => ({
  ResourceAclIdentityRecovery: () => <div data-testid="identity-recovery" />,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
beforeEach(() => {
  mocks.allowed = true
  mocks.enabled = true
  mocks.accountId = 'acme'
  mocks.modes = {}
  mocks.get.mockReset()
  mocks.get.mockImplementation(async (uri: string) => ({
    uri,
    acl_mode: mocks.modes[uri] ?? 'inherit',
    direct_entries: [{ principal: 'user:alice', level: 'read' }],
    inherited_entries: [{ principal: 'user:bob', level: 'write' }],
    effective_entries: [],
  }))
  mocks.list.mockReset()
  mocks.list.mockImplementation(async (uri: string) => ({
    entries:
      uri === root
        ? ['im/', 'volcengine/']
        : uri === `${root}im/`
          ? ['feishu/']
          : [],
  }))
})
afterEach(cleanup)

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const element = () => (
    <QueryClientProvider client={client}>
      <PermissionsPage />
    </QueryClientProvider>
  )
  const view = render(element())
  return { user: userEvent.setup(), refresh: () => view.rerender(element()) }
}

it('keeps grants and inheritance switches out of the directory list', async () => {
  mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  expect(within(row).queryByRole('switch')).toBeNull()
  expect(within(row).queryByText('alice')).toBeNull()
  expect(within(row).queryByText('bob')).toBeNull()
  expect(screen.queryByText('acl.page.granteesColumn')).toBeNull()
  await waitFor(() =>
    expect(within(row).getByText('acl.modes.inherit')).toBeTruthy(),
  )
})

it('allows management in every inheritance mode and browses directories', async () => {
  mocks.modes[`${root}im/`] = 'none'
  const { user } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  const manager = within(row).getByRole('button', {
    name: 'acl.page.manageAction',
  })
  expect(manager.hasAttribute('disabled')).toBe(false)
  await user.click(manager)
  expect(
    within(screen.getByRole('dialog')).getByTestId('editor').textContent,
  ).toBe(`${root}im/`)
  await user.keyboard('{Escape}')
  await user.click(within(row).getByRole('button', { name: 'im' }))
  const child = (await screen.findByRole('button', { name: 'feishu' })).closest(
    'tr',
  )!
  await user.click(
    within(child).getByRole('button', { name: 'acl.page.manageAction' }),
  )
  expect(
    within(screen.getByRole('dialog')).getByTestId('editor').textContent,
  ).toBe(`${root}im/feishu/`)
})

it('opens management even when the row ACL report is denied', async () => {
  mocks.get.mockRejectedValueOnce(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'Denied',
      statusCode: 403,
    }),
  )
  const { user } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  await user.click(
    within(row).getByRole('button', { name: 'acl.page.manageAction' }),
  )
  expect(within(screen.getByRole('dialog')).getByTestId('editor')).toBeTruthy()
})

it('refreshes ACL reports without a local directory cache', async () => {
  const { user, refresh } = mount()
  await user.click(await screen.findByRole('button', { name: 'im' }))
  await screen.findByRole('button', { name: 'feishu' })
  mocks.get.mockClear()
  await user.click(screen.getByRole('button', { name: 'actions.refresh' }))
  await waitFor(() =>
    expect(mocks.get).toHaveBeenCalledWith(`${root}im/feishu/`),
  )
  mocks.accountId = 'other'
  refresh()
  expect(await screen.findByRole('button', { name: 'volcengine' })).toBeTruthy()
  expect(mocks.list).toHaveBeenCalledWith(root)
})

it('shows directory errors and offers retry or identity recovery', async () => {
  mocks.list.mockRejectedValueOnce(new Error('Network unavailable'))
  const { user } = mount()
  expect(await screen.findByText('Network unavailable')).toBeTruthy()
  await user.click(
    screen.getAllByRole('button', { name: 'actions.refresh' })[1],
  )
  expect(await screen.findByRole('button', { name: 'im' })).toBeTruthy()
  cleanup()
  mocks.list.mockRejectedValueOnce(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'Denied',
      statusCode: 403,
    }),
  )
  mount()
  expect(await screen.findByTestId('identity-recovery')).toBeTruthy()
})
