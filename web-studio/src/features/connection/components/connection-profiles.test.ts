import { StudioSetupRequestError } from '#/features/connection/lib/onboarding'
import { OvClientError } from '#/shared/api/ov-client/errors'
import i18n from '#/i18n/core'
// @vitest-environment jsdom
import {
  ConnectionProfilesList,
  groupedConnectionProfiles,
} from '#/features/connection/components/connection-profiles'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import * as React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const connectionState = vi.hoisted(() => ({
  profiles: [] as ConnectionProfile[],
  activeProfileId: 'active',
  studioMode: 'workbench',
  connection: {
    serviceSelection: 'opensource',
    baseUrl: '',
    apiKey: '',
    adminApiKey: '',
    accountId: 'default',
    userId: 'default',
  },
  saveProfile: vi.fn(),
  activateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  navigate: vi.fn(),
}))
const verify = vi.hoisted(() => vi.fn())
vi.mock('#/features/connection/lib/onboarding', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  verifyStudioSetup: verify,
}))
vi.mock('#/features/connection/components/setup-identity-directory', () => ({
  SetupIdentityDirectory: ({
    onSelect,
  }: {
    onSelect: (identity: { accountId: string; userId: string }) => void
  }) =>
    React.createElement(
      'button',
      {
        type: 'button',
        onClick: () => onSelect({ accountId: 'team', userId: 'bob' }),
      },
      'Select Bob',
    ),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => connectionState.navigate,
  Link: ({
    to,
    children,
    ...props
  }: {
    to: string
    children: React.ReactNode
  }) => React.createElement('a', { href: to, ...props }, children),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => connectionState,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const hosted = (
  id: string,
  accountId: string,
  userId = 'wld2',
  apiKey = 'key',
): ConnectionProfile => ({
  id,
  name: id,
  connection: {
    serviceSelection: 'volcengine',
    baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
    apiKey,
    adminApiKey: '',
    accountId,
    userId,
  },
})

beforeEach(() => {
  vi.clearAllMocks()
  verify.mockReset()
  connectionState.profiles = []
  connectionState.activeProfileId = 'active'
  connectionState.studioMode = 'workbench'
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('groups identical connections without losing any saved profile', () => {
  const profiles = [
    hosted('duplicate', 'study'),
    hosted('active', 'study'),
    hosted('other-user', 'study', 'alice'),
    hosted('other-space', 'production'),
  ]
  expect(
    groupedConnectionProfiles(profiles, 'active').map((group) =>
      group.map((profile) => profile.id),
    ),
  ).toEqual([['active', 'duplicate'], ['other-user'], ['other-space']])
})

it('keeps unverified default identities separate when their keys differ', () => {
  connectionState.profiles = [
    hosted('active', 'default', 'default', 'first-key'),
    hosted('other', 'default', 'default', 'second-key'),
  ]

  expect(
    groupedConnectionProfiles(
      connectionState.profiles,
      connectionState.activeProfileId,
    ).map((group) => group.map((profile) => profile.id)),
  ).toEqual([['active'], ['other']])

  render(React.createElement(ConnectionProfilesList))
  expect(screen.getAllByRole('button', { name: 'profiles.edit' })).toHaveLength(
    2,
  )
  fireEvent.click(
    screen.getAllByRole('button', { name: 'profiles.activate' })[1],
  )
  expect(connectionState.activateProfile).toHaveBeenCalledWith('other')
})

it('compacts default/default and shows other hosted account/user identities', () => {
  connectionState.profiles = [
    hosted('personal', 'default', 'default'),
    hosted('enterprise-alice', 'study', 'alice'),
    hosted('enterprise-bob', 'study', 'bob'),
  ]

  render(React.createElement(ConnectionProfilesList))

  expect(screen.getByText('[default]')).toBeTruthy()
  expect(screen.queryByText('[default/default]')).toBeNull()
  expect(screen.getByText('[study/alice]')).toBeTruthy()
  expect(screen.getByText('[study/bob]')).toBeTruthy()
})

it('shows only Account scope in management connection rows and duplicate summaries', () => {
  const profile = hosted('Default', 'team', 'alice')
  connectionState.profiles = [
    profile,
    { ...profile, id: 'duplicate', name: 'team/alice' },
  ]
  connectionState.studioMode = 'management'
  const { container, rerender } = render(
    React.createElement(ConnectionProfilesList),
  )
  expect(screen.queryByText('[team/alice]')).toBeNull()
  expect(screen.queryByText('team/alice')).toBeNull()
  expect(
    within(container.querySelector('summary')!).getByText('[team]'),
  ).toBeTruthy()
  expect(connectionState.profiles[0].connection.userId).toBe('alice')
  connectionState.studioMode = 'workbench'
  rerender(React.createElement(ConnectionProfilesList))
  expect(screen.getAllByText('[team/alice]').length).toBeGreaterThan(0)
})

it('lets every record in an identical connection group be managed', () => {
  connectionState.profiles = [
    hosted('duplicate', 'default', 'default'),
    hosted('active', 'default', 'default'),
  ]

  const { container } = render(React.createElement(ConnectionProfilesList))
  const group = container.querySelector('details')
  expect(group).not.toBeNull()
  expect(group?.open).toBe(false)
  expect(within(group!).getByText('profiles.title · 2')).toBeTruthy()
  expect(
    within(group!.querySelector('summary')!).getByText('[default]'),
  ).toBeTruthy()

  fireEvent.click(group!.querySelector('summary')!)
  expect(group?.open).toBe(true)
  expect(within(group!).queryAllByText('[default]')).toHaveLength(3)
  expect(within(group!).queryByText('[default/default]')).toBeNull()
  expect(
    within(group!).getAllByRole('button', { name: 'profiles.edit' }),
  ).toHaveLength(2)
  expect(
    within(group!).getAllByRole('button', { name: 'profiles.delete' }),
  ).toHaveLength(2)
  fireEvent.click(
    within(group!).getAllByRole('button', { name: 'profiles.activate' })[1],
  )
  expect(connectionState.activateProfile).toHaveBeenCalledWith('duplicate')
})

it('routes adding a profile to its dedicated configuration page', () => {
  render(React.createElement(ConnectionProfilesList))
  expect(
    screen.getByRole('link', { name: 'profiles.add' }).getAttribute('href'),
  ).toBe('/connections/new')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(connectionState.saveProfile).not.toHaveBeenCalled()
})

it('preserves a saved hosted proxy address when editing its name', () => {
  const profile = hosted('proxy', 'team', 'alice')
  profile.connection.baseUrl = 'https://hosted-proxy.example/openviking'
  connectionState.profiles = [profile]
  render(React.createElement(ConnectionProfilesList, { editorId: profile.id }))
  const address = screen.getByDisplayValue(profile.connection.baseUrl)
  expect(address.hasAttribute('readonly')).toBe(true)
  fireEvent.change(screen.getByLabelText('profiles.name'), {
    target: { value: 'Renamed' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
  expect(connectionState.saveProfile).toHaveBeenCalledWith({
    ...profile,
    name: 'Renamed',
  })
  expect(verify).not.toHaveBeenCalled()
})

function selectDirectoryUser() {
  const profile = hosted('active', 'team', 'alice', 'alice-key')
  profile.connection.serviceSelection = 'opensource'
  profile.connection.baseUrl = 'https://service.example'
  profile.connection.adminApiKey = 'root-key'
  connectionState.profiles = [profile]
  render(React.createElement(ConnectionProfilesList, { editorId: profile.id }))
  fireEvent.click(screen.getByRole('button', { name: 'onboarding:verify' }))
  fireEvent.click(screen.getByRole('button', { name: 'Select Bob' }))
  return {
    ...profile,
    connection: { ...profile.connection, userId: 'bob', apiKey: '' },
  }
}

it.each([
  { accountId: 'other-team', userId: 'bob' },
  { accountId: 'team', userId: 'alice' },
])(
  'rejects a key for $accountId/$userId and then saves a matching key',
  async (identity) => {
    const expected = selectDirectoryUser()
    const key = screen.getByLabelText('profiles.fields.apiKey')
    expect((key as HTMLInputElement).value).toBe('')
    verify.mockResolvedValueOnce({ ...expected.connection, ...identity })
    fireEvent.change(key, { target: { value: 'alice-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
    await screen.findByText('onboarding:directoryIdentityMismatch')
    expect(connectionState.saveProfile).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'profiles.edit' })).toBeTruthy()
    verify.mockResolvedValueOnce({ ...expected.connection, apiKey: 'bob-key' })
    fireEvent.change(key, { target: { value: 'bob-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
    await waitFor(() =>
      expect(connectionState.saveProfile).toHaveBeenCalledWith({
        ...expected,
        connection: { ...expected.connection, apiKey: 'bob-key' },
      }),
    )
  },
)

it('saves a directory selection with no data key for management without probing data access', () => {
  const expected = selectDirectoryUser()
  fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
  expect(connectionState.saveProfile).toHaveBeenCalledWith(expected)
  expect(verify).not.toHaveBeenCalled()
})

it.each(['edit', 'cancel'])(
  'ignores verification results after %s',
  async (action) => {
    const expected = selectDirectoryUser()
    let resolve!: (value: unknown) => void
    verify.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
      target: { value: 'bob-key' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
    if (action === 'edit') {
      fireEvent.change(screen.getByLabelText('profiles.name'), {
        target: { value: 'Changed' },
      })
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'profiles.cancel' }))
    }
    await act(async () =>
      resolve({ ...expected.connection, apiKey: 'bob-key' }),
    )
    expect(connectionState.saveProfile).not.toHaveBeenCalled()
  },
)

it('retains the draft and redacts keys when verification fails', async () => {
  selectDirectoryUser()
  verify.mockRejectedValue(new Error('Denied bob-key using root-key'))
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'bob-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Denied')
  expect(alert.textContent).not.toContain('bob-key')
  expect(alert.textContent).not.toContain('root-key')
  expect(connectionState.saveProfile).not.toHaveBeenCalled()
  expect(screen.getByRole('heading', { name: 'profiles.edit' })).toBeTruthy()
})

it('verifies a new connection before saving and activating it inside the connected Studio', async () => {
  const connection = {
    serviceSelection: 'opensource' as const,
    baseUrl: 'https://service.example',
    apiKey: '',
    adminApiKey: 'root-key',
    accountId: 'default',
    userId: 'default',
  }
  verify.mockResolvedValue(connection)
  render(React.createElement(ConnectionProfilesList, { editorId: 'new' }))
  fireEvent.change(screen.getByLabelText('profiles.name'), {
    target: { value: 'My server' },
  })
  fireEvent.change(screen.getByLabelText('profiles.fields.baseUrl'), {
    target: { value: connection.baseUrl },
  })
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'root-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.saveActivate' }))
  await waitFor(() =>
    expect(connectionState.saveProfile).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'My server', connection }),
    ),
  )
  const saved = connectionState.saveProfile.mock.calls[0][0]
  expect(connectionState.activateProfile).toHaveBeenCalledWith(saved.id)
  expect(connectionState.navigate).toHaveBeenCalledWith({ to: '/settings' })
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('navigates editing to a dedicated configuration URL instead of opening a modal', () => {
  connectionState.profiles = [hosted('active', 'team')]
  render(React.createElement(ConnectionProfilesList))
  fireEvent.click(screen.getByRole('button', { name: 'profiles.edit' }))
  expect(connectionState.navigate).toHaveBeenCalledWith({
    to: '/connections/$profileId',
    params: { profileId: 'active' },
  })
  expect(screen.queryByRole('dialog')).toBeNull()
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))

it('keeps a verified request status and redacted details in the profile editor', async () => {
  const expected = selectDirectoryUser()
  const error = new StudioSetupRequestError(
    'health',
    new OvClientError({
      code: 'UNAUTHENTICATED',
      statusCode: 401,
      message: 'Invalid bob-key',
    }),
    { ...expected.connection, apiKey: 'bob-key' },
  )
  verify.mockRejectedValueOnce(error)
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'bob-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))
  const alert = await screen.findByRole('alert')
  expect(alert.querySelector('p')?.textContent).toBe(
    i18n.t('studio:errors.authenticationFailed'),
  )
  expect(alert.textContent).toContain('HTTP 401: Invalid [redacted]')
  expect(alert.textContent).not.toContain('bob-key')
  expect(alert.querySelector('details')?.open).toBe(false)
  expect(connectionState.saveProfile).not.toHaveBeenCalled()
})
