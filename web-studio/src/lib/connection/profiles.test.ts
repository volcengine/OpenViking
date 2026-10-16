// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest'
import {
  readConnectionProfiles,
  persistConnectionProfiles,
  updateActiveProfile,
  normalizeProfileConnection,
  isValidConnectionUrl,
} from '#/lib/connection/profiles'
import type { ConnectionDraft } from '#/lib/connection/types'

const fallback: ConnectionDraft = {
  baseUrl: 'http://10.37.244.186:8020',
  apiKey: 'legacy-key',
  adminApiKey: 'root-key',
  accountId: 'default',
  userId: 'alice',
}
afterEach(() => localStorage.clear())
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
