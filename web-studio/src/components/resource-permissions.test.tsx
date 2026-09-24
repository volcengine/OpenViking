// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { ResourcePermissionsPanel } from './resource-permissions'
import { OvClientError } from '#/lib/ov-client'
import type { AclReport } from '#/lib/resource-acl'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  change: vi.fn(),
  allowed: true,
  enabled: true,
  account: 'acme',
  identity: 'identity-acme',
  users: vi.fn(),
  groups: vi.fn(),
  admins: vi.fn(),
  switchIdentity: vi.fn(),
  serverMode: 'api_key',
}))
vi.mock('#/hooks/use-acl-management', () => ({
  useAclManagement: () => ({
    api: { get: mocks.get, change: mocks.change },
    serverMode: mocks.serverMode,
    switchIdentity: mocks.switchIdentity,
    allowed: mocks.allowed,
    settings: { data: mocks.enabled, isSuccess: true, isFetching: false },
    settingsKey: ['account-acl', mocks.account],
    connection: {
      userId: 'alice',
      accountId: mocks.account,
      baseUrl: 'http://localhost',
      adminApiKey: 'admin',
    },
    adminConnection: { accountId: mocks.account },
    identityScopeKey: mocks.identity,
    aclIdentityScopeKey: mocks.identity,
    useAccountAdminCredential: false,
  }),
}))
vi.mock('./account-acl-settings', () => ({ AccountAclSettings: () => null }))
vi.mock('#/lib/admin', () => ({
  fetchAdminUsersPage: mocks.users,
  fetchAdminUsers: mocks.admins,
  fetchAdminGroups: mocks.groups,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number; level?: string }) =>
      key === 'acl.confirmGrant'
        ? `${key}:${options?.count}:${options?.level}`
        : key === 'acl.confirmEveryoneGrant'
          ? `${key}:${options?.level}`
          : key,
  }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const uri = 'viking://resources/project'
let report: AclReport
beforeEach(() => {
  vi.resetAllMocks()
  mocks.allowed = true
  mocks.enabled = true
  mocks.account = 'acme'
  mocks.identity = 'identity-acme'
  mocks.serverMode = 'api_key'
  mocks.admins.mockResolvedValue([
    { userId: 'owner', role: 'admin', apiKey: 'user-key' },
    { userId: 'root', role: 'root' },
  ])
  report = {
    uri,
    acl_mode: 'inherit',
    direct_entries: [{ principal: 'user:bob', level: 'read' }],
    inherited_entries: [{ principal: 'group:engineering', level: 'write' }],
    effective_entries: [
      { principal: 'user:bob', level: 'read' },
      { principal: 'group:engineering', level: 'write' },
    ],
  }
  mocks.get.mockImplementation(async () => report)
  mocks.change.mockImplementation(async (_uri, change) => {
    report = {
      ...report,
      ...(change.kind === 'mode'
        ? { acl_mode: change.mode }
        : { direct_entries: [] }),
    }
    report.effective_entries =
      report.acl_mode === 'restricted'
        ? report.direct_entries
        : [...report.direct_entries, ...report.inherited_entries]
    return report
  })
  mocks.users.mockResolvedValue({ users: [{ userId: 'bob' }], total: 1 })
  mocks.groups.mockResolvedValue([{ group_id: 'engineering', member_count: 1 }])
})
afterEach(cleanup)
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createRouter({
    basepath: '/studio',
    history: createMemoryHistory({ initialEntries: ['/studio/'] }),
    routeTree: createRootRoute({
      component: () => (
        <QueryClientProvider client={client}>
          <ResourcePermissionsPanel uri={uri} />
        </QueryClientProvider>
      ),
    }),
  })
  const view = render(<RouterProvider router={router} />)
  return { ...view, client, user: userEvent.setup() }
}
it('keeps the no-admin recovery link inside the Studio base path', async () => {
  mocks.admins.mockResolvedValue([])
  mocks.get.mockRejectedValue(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'Denied',
      statusCode: 403,
    }),
  )
  mount()
  const link = await screen.findByRole('link', { name: 'acl.recovery.users' })
  expect(link.getAttribute('href')).toBe('/studio/users')
})
it('shows current access and source of each grant without explanatory panels', async () => {
  mount()
  await screen.findByText('bob')
  expect(screen.getByText('acl.grantSubjectColumn')).toBeTruthy()
  expect(screen.getByText('acl.grantLevelColumn')).toBeTruthy()
  expect(screen.getByText('acl.grantSourceColumn')).toBeTruthy()
  expect(screen.getByText(/acl.subjects.user/)).toBeTruthy()
  expect(screen.getByText('acl.levels.read')).toBeTruthy()
  expect(screen.getByText('acl.peopleWithAccess')).toBeTruthy()
  expect(screen.getByText('engineering')).toBeTruthy()
  expect(screen.getByText(/acl.inheritedSource/)).toBeTruthy()
  expect(screen.queryByText('acl.page.grantScope')).toBeNull()
})
it('shows the ancestor directory when an inherited grant can be traced', async () => {
  mocks.get.mockImplementation(async (target: string) =>
    target === 'viking://resources'
      ? {
          uri: target,
          acl_mode: 'inherit',
          direct_entries: [{ principal: 'group:engineering', level: 'write' }],
          inherited_entries: [],
          effective_entries: [],
        }
      : report,
  )
  mount()
  expect(await screen.findByText('resources')).toBeTruthy()
  expect(screen.getByText('acl.accountAdministrators')).toBeTruthy()
  expect(screen.getByText('acl.administratorSource')).toBeTruthy()
})
it('shows one effective grant with both sources when a principal is also granted directly', async () => {
  report = {
    ...report,
    direct_entries: [{ principal: 'group:engineering', level: 'read' }],
    effective_entries: [{ principal: 'group:engineering', level: 'write' }],
  }
  mount()
  const row = (await screen.findByText('engineering')).closest('tr')!
  expect(screen.getAllByText('engineering')).toHaveLength(1)
  expect(within(row).getByText('acl.directAndInheritedSource')).toBeTruthy()
  expect(within(row).getByText('acl.effectiveLevel')).toBeTruthy()
  expect(within(row).getByText('acl.levels.read')).toBeTruthy()
  expect(
    within(row).getByRole('button', { name: 'acl.removeFor' }),
  ).toBeTruthy()
})
it('allows adding direct grants while inheritance is enabled by default', async () => {
  report = {
    ...report,
    acl_mode: 'none',
    direct_entries: [],
    inherited_entries: [],
    effective_entries: [],
  }
  mount()
  await screen.findByText('acl.everyone')
  expect(screen.getByText('acl.defaultRule')).toBeTruthy()
  expect(screen.queryByText('acl.onlyAdmins')).toBeNull()
  expect(screen.getByRole('button', { name: 'acl.addGrant' })).toBeTruthy()
  expect(
    screen
      .getByRole('switch', { name: 'acl.inheritParent' })
      .getAttribute('aria-checked'),
  ).toBe('true')
})
it('confirms direct removal and preserves inherited permissions in the display', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.removeFor' }))
  expect(mocks.change).not.toHaveBeenCalled()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(uri, {
      kind: 'revoke',
      principal: 'user:bob',
    }),
  )
  expect(screen.getByText('engineering')).toBeTruthy()
})
it('keeps restricted grants and shows inheritance disabled', async () => {
  report = {
    ...report,
    acl_mode: 'restricted',
    effective_entries: report.direct_entries,
  }
  mount()
  await screen.findByText('bob')
  expect(
    screen
      .getByRole('switch', { name: 'acl.inheritParent' })
      .getAttribute('aria-checked'),
  ).toBe('false')
  expect(screen.queryByText('engineering')).toBeNull()
})
it('explains inheritance and changes only the ACL mode, preserving direct grants', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.inheritHelpLabel' }))
  expect(screen.getByText('acl.inheritHelp')).toBeTruthy()
  const toggle = screen.getByRole('switch', { name: 'acl.inheritParent' })
  await user.click(toggle)
  expect(screen.getByText('acl.restrictWarning')).toBeTruthy()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(uri, {
      kind: 'mode',
      mode: 'restricted',
    }),
  )
  expect(screen.getByText('bob')).toBeTruthy()
  expect(screen.queryByText('engineering')).toBeNull()
  await user.click(toggle)
  expect(screen.getByText('acl.restoreWarning')).toBeTruthy()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'acl.confirm',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(uri, {
      kind: 'mode',
      mode: 'inherit',
    }),
  )
  expect(screen.getByText('engineering')).toBeTruthy()
  expect(screen.getByText('bob')).toBeTruthy()
})
it('does not permit editing when account ACL is disabled', async () => {
  mocks.enabled = false
  mount()
  await screen.findByText('bob')
  expect(
    screen
      .getByRole('button', { name: 'acl.removeFor' })
      .hasAttribute('disabled'),
  ).toBe(true)
  expect(
    screen
      .getByRole('button', { name: 'acl.addGrant' })
      .hasAttribute('disabled'),
  ).toBe(true)
  expect(
    screen
      .getByRole('switch', { name: 'acl.inheritParent' })
      .hasAttribute('data-disabled'),
  ).toBe(true)
  expect(mocks.users).not.toHaveBeenCalled()
})
it('shows a local request error and retries without affecting the resource page', async () => {
  mocks.get.mockRejectedValueOnce(new Error('Network unavailable'))
  const { user } = mount()
  await screen.findByText('Network unavailable')
  await user.click(screen.getByRole('button', { name: 'actions.refresh' }))
  await screen.findByText('bob')
})
it('grants edit permission to a selected account group', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.addGrant' }))
  await user.click(screen.getByRole('button', { name: 'acl.subjects.group' }))
  await waitFor(() => expect(mocks.groups).toHaveBeenCalled())
  await user.click(screen.getByRole('button', { name: 'engineering' }))
  await user.click(screen.getByRole('button', { name: 'acl.levels.write' }))
  await user.click(
    screen.getByRole('button', {
      name: 'acl.confirmGrant:1:acl.levels.write',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(uri, {
      kind: 'grant',
      principal: 'group:engineering',
      level: 'write',
    }),
  )
})
it('selects multiple users and grants the same level to each', async () => {
  mocks.users.mockResolvedValue({
    users: [{ userId: 'bob' }, { userId: 'carol' }],
    total: 2,
  })
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.addGrant' }))
  await user.click(await screen.findByRole('button', { name: 'carol' }))
  await user.click(screen.getByRole('button', { name: 'bob' }))
  await user.click(screen.getByRole('button', { name: 'acl.levels.write' }))
  await user.click(
    screen.getByRole('button', {
      name: 'acl.confirmGrant:2:acl.levels.write',
    }),
  )
  await waitFor(() => expect(mocks.change).toHaveBeenCalledTimes(2))
  expect(mocks.change.mock.calls.map(([, change]) => change)).toEqual([
    { kind: 'grant', principal: 'user:carol', level: 'write' },
    { kind: 'grant', principal: 'user:bob', level: 'write' },
  ])
})
it('adds permissions within the same panel and returns to the grant list', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.addGrant' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'acl.backToGrants' })).toBeTruthy()
  expect(screen.getByRole('group', { name: 'acl.level' })).toBeTruthy()
  await user.click(
    screen.getByRole('button', { name: 'acl.subjects.everyoneShort' }),
  )
  expect(screen.getByRole('group', { name: 'acl.level' })).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'acl.backToGrants' }))
  expect(screen.getByText('bob')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /acl.confirmGrant/ })).toBeNull()
})
it('grants permission to all account users without selecting a candidate', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.addGrant' }))
  await user.click(
    screen.getByRole('button', { name: 'acl.subjects.everyoneShort' }),
  )
  expect(screen.queryByLabelText('acl.search')).toBeNull()
  await user.click(
    screen.getByRole('button', {
      name: 'acl.confirmEveryoneGrant:acl.levels.read',
    }),
  )
  await waitFor(() =>
    expect(mocks.change).toHaveBeenCalledWith(uri, {
      kind: 'grant',
      principal: 'user:*',
      level: 'read',
    }),
  )
})

it('offers an existing account admin after a 403 without changing grants', async () => {
  mocks.get.mockRejectedValue(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'ACL management denied',
      statusCode: 403,
    }),
  )
  const { user } = mount()
  expect(
    await screen.findByRole('combobox', { name: 'acl.recovery.admin' }),
  ).toBeTruthy()
  expect(
    screen.getByRole('combobox', { name: 'acl.recovery.admin' }).textContent,
  ).toContain('owner')
  expect(screen.queryByRole('option', { name: 'root' })).toBeNull()
  expect(screen.queryByText('acl.loadFailed')).toBeNull()
  expect(mocks.switchIdentity).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'acl.recovery.switch' }))
  await waitFor(() =>
    expect(mocks.switchIdentity).toHaveBeenCalledWith({
      accountId: 'acme',
      userId: 'owner',
      apiKey: 'user-key',
      allowLegacyIdentityFallback: true,
    }),
  )
  expect(mocks.change).not.toHaveBeenCalled()
})
it('requires a user key when the admin key is unavailable', async () => {
  mocks.admins.mockResolvedValue([{ userId: 'owner', role: 'admin' }])
  mocks.get.mockRejectedValue(
    new OvClientError({
      code: 'PERMISSION_DENIED',
      message: 'Denied',
      statusCode: 403,
    }),
  )
  const { user } = mount()
  await screen.findByRole('combobox', { name: 'acl.recovery.admin' })
  const button = screen.getByRole('button', { name: 'acl.recovery.switch' })
  expect(button.hasAttribute('disabled')).toBe(true)
  await user.type(
    screen.getByLabelText('acl.recovery.key'),
    'existing-user-key',
  )
  await user.click(button)
  await waitFor(() =>
    expect(mocks.switchIdentity).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: 'existing-user-key' }),
    ),
  )
})
it('does not suggest switching identities for a network error', async () => {
  mocks.get.mockRejectedValue(new Error('Network unavailable'))
  mount()
  await screen.findByText('Network unavailable')
  expect(screen.queryByText('acl.recovery.title')).toBeNull()
  expect(mocks.admins).not.toHaveBeenCalled()
})

it('clears hidden selections when switching from users to groups', async () => {
  const { user } = mount()
  await screen.findByText('bob')
  await user.click(screen.getByRole('button', { name: 'acl.addGrant' }))
  await user.click(await screen.findByRole('button', { name: 'bob' }))
  await user.click(screen.getByRole('button', { name: 'acl.subjects.group' }))
  expect(
    screen
      .getByRole('button', { name: 'acl.confirmGrant:0:acl.levels.read' })
      .hasAttribute('disabled'),
  ).toBe(true)
  await user.click(await screen.findByRole('button', { name: 'engineering' }))
  await user.click(screen.getByRole('button', { name: 'acl.levels.manage' }))
  await user.click(
    screen.getByRole('button', {
      name: 'acl.confirmGrant:1:acl.levels.manage',
    }),
  )
  await waitFor(() => expect(mocks.change).toHaveBeenCalledTimes(1))
  expect(mocks.change).toHaveBeenCalledWith(uri, {
    kind: 'grant',
    principal: 'group:engineering',
    level: 'manage',
  })
})
