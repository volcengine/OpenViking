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
  settingsFetching: false,
  modes: {} as Record<string, 'none' | 'inherit' | 'restricted'>,
  list: vi.fn(),
  change: vi.fn(),
  get: vi.fn(async (uri: string) => ({
    uri,
    acl_mode: mocks.modes[uri] ?? 'inherit',
    direct_entries: [{ principal: 'user:alice', level: 'read' }],
    inherited_entries: [],
    effective_entries: [],
  })),
}))
vi.mock('#/hooks/use-acl-management', () => ({
  useAclManagement: () => ({
    allowed: mocks.allowed,
    settings: {
      data: mocks.enabled,
      isSuccess: true,
      isFetching: mocks.settingsFetching,
    },
    settingsKey: ['account-acl', mocks.accountId],
    connection: { baseUrl: 'http://localhost', accountId: mocks.accountId },
    identityScopeKey: mocks.accountId,
    aclIdentityScopeKey: mocks.accountId,
    api: { get: mocks.get, listDirectory: mocks.list, change: mocks.change },
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
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
beforeEach(() => {
  mocks.allowed = true
  mocks.enabled = true
  mocks.settingsFetching = false
  mocks.accountId = 'acme'
  mocks.modes = {}
  mocks.list.mockReset()
  mocks.get.mockReset()
  mocks.get.mockImplementation(async (uri: string) => ({
    uri,
    acl_mode: mocks.modes[uri] ?? 'inherit',
    direct_entries: [{ principal: 'user:alice', level: 'read' }],
    inherited_entries: [],
    effective_entries: [],
  }))
  mocks.change.mockReset()
  mocks.change.mockImplementation(async (uri, change) => {
    mocks.modes[uri] = change.kind === 'reset' ? 'none' : change.mode
    return mocks.get(uri)
  })
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
  return {
    ...view,
    user: userEvent.setup(),
    refresh: () => view.rerender(element()),
  }
}
it('shows parent ACL errors and restores child controls after retry', async () => {
  const getReport = mocks.get.getMockImplementation()!
  let parentFails = true
  mocks.get.mockImplementation(async (uri) => {
    if (uri === `${root}im/` && parentFails) {
      throw new Error('Parent ACL request timed out')
    }
    return getReport(uri)
  })
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'im' }))
  const row = (await screen.findByRole('button', { name: 'feishu' })).closest(
    'tr',
  )!
  const alert = await screen.findByRole('alert')
  expect(within(alert).getByText('acl.page.parentAclFailed')).toBeTruthy()
  expect(within(alert).getByText('Parent ACL request timed out')).toBeTruthy()
  const toggle = await within(row).findByRole('switch')
  const manager = within(row).getByRole('button', {
    name: 'acl.page.manageAction',
  })
  expect(toggle.hasAttribute('data-disabled')).toBe(true)
  expect(manager.hasAttribute('disabled')).toBe(true)
  parentFails = false
  await user.click(
    within(alert).getByRole('button', { name: 'actions.refresh' }),
  )
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(toggle.hasAttribute('data-disabled')).toBe(false)
  expect(manager.hasAttribute('disabled')).toBe(false)
})

it('discards an open confirmation when account ACL is disabled', async () => {
  const { user, refresh } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  await user.click(await within(row).findByRole('switch'))
  expect(screen.getByRole('alertdialog')).toBeTruthy()
  mocks.enabled = false
  refresh()
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  expect(within(row).getByRole('switch').hasAttribute('data-disabled')).toBe(
    true,
  )
  expect(mocks.change).not.toHaveBeenCalled()
  mocks.enabled = true
  refresh()
  expect(screen.queryByRole('alertdialog')).toBeNull()
})

it('blocks directory changes while account settings are refreshing', async () => {
  const { user, refresh } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  const toggle = await within(row).findByRole('switch')
  mocks.settingsFetching = true
  refresh()
  expect(toggle.hasAttribute('data-disabled')).toBe(true)
  await user.click(toggle)
  expect(screen.queryByRole('alertdialog')).toBeNull()
  expect(mocks.change).not.toHaveBeenCalled()
  mocks.settingsFetching = false
  refresh()
  expect(toggle.hasAttribute('data-disabled')).toBe(false)
})

it('browses resources one directory at a time and edits permissions separately', async () => {
  const { user } = mount()
  expect(await screen.findByRole('button', { name: 'im' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'volcengine' })).toBeTruthy()
  expect(
    screen.queryByRole('button', { name: 'acl.page.addDirectory' }),
  ).toBeNull()
  await waitFor(() =>
    expect(
      screen.getAllByRole('button', { name: 'acl.page.manageAction' }),
    ).toHaveLength(2),
  )

  await user.click(screen.getByRole('button', { name: 'im' }))
  expect(await screen.findByRole('button', { name: 'feishu' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'volcengine' })).toBeNull()
  await user.click(
    screen.getByRole('button', { name: 'acl.page.manageAction' }),
  )
  expect(
    within(screen.getByRole('dialog')).getByTestId('editor').textContent,
  ).toBe(`${root}im/feishu/`)
  await user.keyboard('{Escape}')
  await user.click(screen.getByRole('button', { name: 'acl.page.back' }))
  expect(await screen.findByRole('button', { name: 'volcengine' })).toBeTruthy()
})
it('shows recipient and permission level in a wider summary column', async () => {
  mocks.get.mockImplementation(async (uri: string) => ({
    uri,
    acl_mode: 'restricted',
    direct_entries: [
      { principal: 'group:fe-dev', level: 'read' },
      { principal: 'user:anonymous', level: 'write' },
      { principal: 'user:alice', level: 'manage' },
      { principal: 'user:bob', level: 'read' },
    ],
    inherited_entries: [],
    effective_entries: [],
  }))
  mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  await waitFor(() => expect(within(row).getByText('+1')).toBeTruthy())
  expect(within(row).getByText('fe-dev')).toBeTruthy()
  expect(within(row).getByText('anonymous')).toBeTruthy()
  expect(within(row).getByText('alice')).toBeTruthy()
  expect(within(row).getByText('acl.levels.read')).toBeTruthy()
  expect(within(row).getByText('acl.levels.write')).toBeTruthy()
  expect(within(row).getByText('acl.levels.manage')).toBeTruthy()
  expect(within(row).queryByText('bob')).toBeNull()
  expect(within(row).getByText('+1').closest('td')?.getAttribute('title')).toBe(
    'acl.subjects.group · fe-dev: acl.levels.read, acl.subjects.user · anonymous: acl.levels.write, acl.subjects.user · alice: acl.levels.manage, acl.subjects.user · bob: acl.levels.read',
  )
})
it('enables permission management only after restricted access is switched on', async () => {
  mocks.modes[`${root}im/`] = 'none'
  const { user } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  const manager = within(row).getByRole('button', {
    name: 'acl.page.manageAction',
  })
  expect(manager.hasAttribute('disabled')).toBe(true)
  await user.click(await within(row).findByRole('switch'))
  expect(mocks.change).not.toHaveBeenCalled()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(`${root}im/`, {
      kind: 'mode',
      mode: 'restricted',
    }),
  )
  await waitFor(() => expect(manager.hasAttribute('disabled')).toBe(false))
})
it('turns off an independent restriction and disables its management action', async () => {
  const { user } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  await user.click(await within(row).findByRole('switch'))
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(`${root}im/`, { kind: 'reset' }),
  )
  await waitFor(() =>
    expect(
      within(row)
        .getByRole('button', { name: 'acl.page.manageAction' })
        .hasAttribute('disabled'),
    ).toBe(true),
  )
})
it('lets a child stop and restore inheritance without clearing its grants', async () => {
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'im' }))
  const row = (await screen.findByRole('button', { name: 'feishu' })).closest(
    'tr',
  )!
  await waitFor(() => expect(within(row).getByRole('switch')).toBeTruthy())
  expect(within(row).getByText('acl.modes.inherit')).toBeTruthy()
  await user.click(within(row).getByRole('switch'))
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(`${root}im/feishu/`, {
      kind: 'mode',
      mode: 'restricted',
    }),
  )
  await user.click(within(row).getByRole('switch'))
  expect(
    within(screen.getByRole('alertdialog')).getByText(
      'acl.page.restoreInheritanceWarning',
    ),
  ).toBeTruthy()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(`${root}im/feishu/`, {
      kind: 'mode',
      mode: 'inherit',
    }),
  )
  expect(
    within(row)
      .getByRole('button', { name: 'acl.page.manageAction' })
      .hasAttribute('disabled'),
  ).toBe(false)
})
it('loads the tree from the server for each account, without a browser-maintained directory list', async () => {
  const view = mount()
  await screen.findByRole('button', { name: 'im' })
  await view.user.click(screen.getByRole('button', { name: 'im' }))
  await screen.findByRole('button', { name: 'feishu' })
  mocks.accountId = 'other'
  view.refresh()
  expect(await screen.findByRole('button', { name: 'volcengine' })).toBeTruthy()
  expect(mocks.list).toHaveBeenCalledWith(root)
})
it('shows a directory listing error with a retry action', async () => {
  mocks.list.mockRejectedValueOnce(new Error('Network unavailable'))
  const { user } = mount()
  expect(await screen.findByText('Network unavailable')).toBeTruthy()
  await user.click(
    screen.getAllByRole('button', { name: 'actions.refresh' })[1],
  )
  expect(await screen.findByRole('button', { name: 'im' })).toBeTruthy()
})
it('offers identity recovery when listing directories is denied', async () => {
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
it('opens the permission drawer when a row ACL report is denied', async () => {
  mocks.get.mockRejectedValueOnce(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'Denied',
      statusCode: 403,
    }),
  )
  const { user } = mount()
  const row = (await screen.findByRole('button', { name: 'im' })).closest('tr')!
  const manager = within(row).getByRole('button', {
    name: 'acl.page.manageAction',
  })
  await waitFor(() => expect(manager.hasAttribute('disabled')).toBe(false))
  await user.click(manager)
  expect(within(screen.getByRole('dialog')).getByTestId('editor')).toBeTruthy()
})
it('keeps advanced account settings in the header and shows inline controls when disabled', () => {
  const { refresh } = mount()
  expect(screen.queryByTestId('account-settings')).toBeNull()
  expect(screen.getByTestId('advanced-settings')).toBeTruthy()
  mocks.enabled = false
  refresh()
  expect(screen.getByTestId('account-settings')).toBeTruthy()
  expect(screen.queryByTestId('advanced-settings')).toBeNull()
})

it('refreshes parent and child ACL reports even when directory entries are unchanged', async () => {
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'im' }))
  await screen.findByRole('button', { name: 'feishu' })
  await waitFor(() =>
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(false),
  )
  mocks.get.mockClear()
  mocks.modes[`${root}im/`] = 'none'
  mocks.get.mockImplementation(async (uri: string) => ({
    uri,
    acl_mode: mocks.modes[uri] ?? 'restricted',
    direct_entries: [{ principal: 'user:bob', level: 'manage' }],
    inherited_entries: [],
    effective_entries: [],
  }))
  await user.click(screen.getByRole('button', { name: 'actions.refresh' }))
  await waitFor(() => {
    expect(mocks.get).toHaveBeenCalledWith(`${root}im/`)
    expect(mocks.get).toHaveBeenCalledWith(`${root}im/feishu/`)
    expect(screen.getByText('bob')).toBeTruthy()
    expect(screen.getByText('acl.modes.restricted')).toBeTruthy()
  })
  await user.click(screen.getByRole('switch'))
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(`${root}im/feishu/`, {
      kind: 'reset',
    }),
  )
})
