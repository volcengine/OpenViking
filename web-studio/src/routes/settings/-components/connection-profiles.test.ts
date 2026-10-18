// @vitest-environment jsdom
import * as React from 'react'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ConnectionProfile } from '#/lib/connection/profiles'
import {
  ConnectionProfilesList,
  groupedConnectionProfiles,
} from './connection-profiles'

const connectionState = vi.hoisted(() => ({
  profiles: [] as ConnectionProfile[],
  activeProfileId: 'active',
  saveProfile: vi.fn(),
  activateProfile: vi.fn(),
  deleteProfile: vi.fn(),
}))

vi.mock('#/hooks/use-app-connection', () => ({
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
  connectionState.profiles = []
  connectionState.activeProfileId = 'active'
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

it('creates a connection on HTTP origins without crypto.randomUUID', () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(
    globalThis.crypto,
  )
  vi.stubGlobal('crypto', { getRandomValues })

  render(React.createElement(ConnectionProfilesList))
  fireEvent.click(screen.getByRole('button', { name: 'profiles.add' }))
  fireEvent.change(screen.getByLabelText('profiles.name'), {
    target: { value: 'Local' },
  })
  fireEvent.change(screen.getByLabelText('profiles.fields.baseUrl'), {
    target: { value: 'http://192.0.2.1:1933' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.save' }))

  expect(connectionState.saveProfile).toHaveBeenCalledWith(
    expect.objectContaining({
      id: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      ),
      name: 'Local',
    }),
  )
})
