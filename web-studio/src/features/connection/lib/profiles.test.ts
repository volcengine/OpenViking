// @vitest-environment jsdom
import {
  activateIdentityProfile,
  groupProfilesByService,
  isValidConnectionUrl,
  normalizeProfileConnection,
  persistConnectionProfiles,
  readConnectionProfiles,
  savedDataIdentities,
  updateActiveProfile,
} from '#/features/connection/lib/profiles'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import { afterEach, expect, it, vi } from 'vitest'

const fallback: ConnectionDraft = {
  baseUrl: 'http://10.37.244.186:8020',
  apiKey: 'legacy-key',
  adminApiKey: 'root-key',
  accountId: 'default',
  userId: 'alice',
}
afterEach(() => localStorage.clear())
it('groups navigation by service address and provider regardless of credentials or identity', () => {
  const original = { id: 'first', name: 'Development', connection: fallback }
  const anotherUser = {
    id: 'second',
    name: 'other/bob',
    connection: {
      ...fallback,
      baseUrl: `${fallback.baseUrl}/`,
      accountId: 'other',
      userId: 'bob',
      apiKey: 'bob-key',
      adminApiKey: '',
    },
  }
  const anotherPath = {
    ...original,
    id: 'path',
    connection: { ...fallback, baseUrl: `${fallback.baseUrl}/another-service` },
  }
  const anotherProvider = {
    ...original,
    id: 'provider',
    connection: { ...fallback, serviceSelection: 'volcengine' as const },
  }
  const profiles = [original, anotherUser, anotherPath, anotherProvider]
  expect(groupProfilesByService(profiles)).toEqual([
    [original, anotherUser],
    [anotherPath],
    [anotherProvider],
  ])
  // Grouping never merges stored credentials or rewrites the saved profiles.
  expect(profiles).toHaveLength(4)
  expect(anotherUser.connection.adminApiKey).toBe('')
})
it('aligns saved and restored provider labels with official hosted authentication', () => {
  const hosted = {
    ...fallback,
    baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
    serviceSelection: 'opensource' as const,
  }
  expect(normalizeProfileConnection(hosted).serviceSelection).toBe('volcengine')
  const state = readConnectionProfiles(fallback)
  state.profiles[0].connection = hosted
  persistConnectionProfiles(state)
  expect(
    readConnectionProfiles(fallback).profiles[0].connection.serviceSelection,
  ).toBe('volcengine')
  expect(
    normalizeProfileConnection({ ...fallback, serviceSelection: 'volcengine' })
      .serviceSelection,
  ).toBe('volcengine')
})
it.each(['', 'not-url', 'file:///tmp/ov', 'https://name:secret@example.com'])(
  'rejects invalid or credential-bearing address %s',
  (value) => {
    expect(isValidConnectionUrl(value)).toBe(false)
  },
)
it.each([
  'http://10.0.0.1:1933',
  ' https://api.vikingdb.cn-beijing.volces.com/openviking/ ',
])('accepts service address %s', (value) => {
  expect(isValidConnectionUrl(value)).toBe(true)
})
it('migrates the legacy connection with credentials and an explicit service type', () => {
  const value = readConnectionProfiles(fallback)
  expect(value.profiles[0].connection).toEqual({
    ...fallback,
    serviceSelection: 'opensource',
  })
  expect(
    readConnectionProfiles({
      ...fallback,
      baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
    }).profiles[0].connection.serviceSelection,
  ).toBe('volcengine')
})
it('restores the enabled profile instead of overwriting it with startup defaults', () => {
  const value = readConnectionProfiles(fallback)
  value.profiles.push({
    id: 'cloud',
    name: 'Cloud',
    connection: {
      ...fallback,
      serviceSelection: 'volcengine',
      apiKey: 'cloud-key',
    },
  })
  value.activeId = 'cloud'
  persistConnectionProfiles(value)
  expect(readConnectionProfiles({ ...fallback, apiKey: 'env-key' })).toEqual(
    value,
  )
  const changed = updateActiveProfile(value, {
    ...value.profiles[1].connection,
    userId: 'resolved-user',
  })
  expect(changed.profiles[0]).toEqual(value.profiles[0])
  expect(changed.profiles[1].connection.userId).toBe('resolved-user')
})
it('recovers a missing active ID and ignores malformed records without losing the legacy key', () => {
  const value = readConnectionProfiles(fallback)
  persistConnectionProfiles({ ...value, activeId: 'missing' })
  expect(readConnectionProfiles(fallback).activeId).toBe('default')
  localStorage.setItem('ov_console_connection_profiles', '{ broken')
  expect(readConnectionProfiles(fallback).profiles[0].connection.apiKey).toBe(
    'legacy-key',
  )
})

it('preserves identities on one endpoint and reuses an exact saved identity', () => {
  const initial = readConnectionProfiles(fallback)
  const next = { ...fallback, userId: 'bob', apiKey: 'bob-key' }
  const switched = activateIdentityProfile(initial, next)
  expect(switched.profiles).toHaveLength(2)
  expect(switched.profiles[0]).toEqual(initial.profiles[0])
  expect(switched.activeId).not.toBe(initial.activeId)
  const restored = activateIdentityProfile(switched, fallback)
  expect(restored.activeId).toBe(initial.activeId)
  expect(restored.profiles).toHaveLength(2)
  const otherCredential = activateIdentityProfile(switched, {
    ...next,
    apiKey: 'other-key',
  })
  expect(otherCredential.profiles).toHaveLength(3)
})

it('retains configured User keys across scope changes without adding connection configurations', () => {
  const initial = readConnectionProfiles(fallback)
  const bob = { ...fallback, userId: 'bob', apiKey: 'bob-key' }
  const updated = updateActiveProfile(initial, bob, true)
  expect(updated.activeId).toBe(initial.activeId)
  expect(updated.profiles).toHaveLength(1)
  persistConnectionProfiles(updated)
  const restored = readConnectionProfiles(bob)
  expect(
    savedDataIdentities(restored.profiles, bob).map((entry) => entry.userId),
  ).toEqual(['bob', fallback.userId])
})
it('isolates remembered User keys by service and excludes Root-only or keyless identities', () => {
  const initial = readConnectionProfiles(fallback)
  const updated = updateActiveProfile(
    initial,
    {
      ...fallback,
      baseUrl: 'http://other.test',
      userId: 'bob',
      apiKey: 'bob-key',
    },
    true,
  )
  expect(
    savedDataIdentities(updated.profiles, fallback).map(
      (entry) => entry.userId,
    ),
  ).toEqual([fallback.userId])
  const rootOnly = { ...fallback, apiKey: '', adminApiKey: 'root-key' }
  expect(
    savedDataIdentities(
      [{ id: 'root', name: 'Root', connection: rootOnly }],
      rootOnly,
    ),
  ).toEqual([])
})

it('does not retain a replaced or cleared configured key as a selectable history entry', () => {
  const initial = readConnectionProfiles(fallback)
  const bob = { ...fallback, userId: 'bob', apiKey: 'bob-key' }
  const switched = updateActiveProfile(
    updateActiveProfile(initial, bob, true),
    fallback,
    true,
  )
  const cleared = updateActiveProfile(switched, { ...fallback, apiKey: '' })
  expect(
    savedDataIdentities(cleared.profiles, { ...fallback, apiKey: '' }).some(
      (entry) => entry.apiKey === fallback.apiKey,
    ),
  ).toBe(false)
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))
