import {
  rememberSurface,
  restoreSurface,
} from '#/features/connection/lib/surface-context'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import { expect, it } from 'vitest'

const profile: ConnectionProfile = {
  id: 'one',
  name: 'Server',
  connection: {
    serviceSelection: 'opensource',
    baseUrl: 'https://ov.test',
    accountId: 'a',
    userId: 'alice',
    apiKey: 'alice-key',
    adminApiKey: 'root',
  },
}
it('restores workbench Account and User independently of the managed Account, including after a storage round trip', () => {
  const before = rememberSurface(profile, profile.connection, 'workbench')
  const management = rememberSurface(
    before,
    { ...profile.connection, accountId: 'b', userId: '', apiKey: '' },
    'management',
  )
  const saved = JSON.parse(JSON.stringify(management)) as ConnectionProfile
  const workbench = restoreSurface(saved, 'workbench')
  expect(workbench).toMatchObject({
    accountId: 'a',
    userId: 'alice',
    apiKey: 'alice-key',
    adminApiKey: 'root',
  })
  expect(
    restoreSurface({ ...saved, connection: workbench }, 'management'),
  ).toMatchObject({ accountId: 'b', userId: '', apiKey: '' })
})
it('never picks a remembered key belonging to another service for management resource access', () => {
  const next = restoreSurface(
    {
      ...profile,
      managementAccountId: 'b',
      identities: [
        {
          ...profile.connection,
          accountId: 'b',
          baseUrl: 'https://other.test',
          apiKey: 'other-service-key',
        },
      ],
    },
    'management',
  )
  expect(next.accountId).toBe('b')
  expect(next.apiKey).toBe('')
})
it('ignores malformed saved data identities and supports legacy profiles', () => {
  expect(restoreSurface(profile, 'workbench')).toBe(profile.connection)
  expect(
    restoreSurface({ ...profile, workbenchIdentity: {} as never }, 'workbench'),
  ).toBe(profile.connection)
})
it('retains trusted asserted users with no tenant Key', () => {
  const trusted = { ...profile.connection, apiKey: '' }
  const saved = rememberSurface(profile, trusted, 'workbench')
  expect(
    restoreSurface(
      {
        ...saved,
        connection: { ...trusted, accountId: 'b', userId: 'default' },
      },
      'workbench',
    ),
  ).toEqual(trusted)
})
