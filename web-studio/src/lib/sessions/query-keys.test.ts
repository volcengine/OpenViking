import { describe, expect, it } from 'vitest'

import type { ConnectionDraft } from '#/hooks/use-app-connection'
import { getSessionScopeKey } from './query-keys'

function connection(
  overrides: Partial<ConnectionDraft> = {},
): ConnectionDraft {
  return {
    accountId: 'default',
    adminApiKey: '',
    apiKey: '',
    baseUrl: 'http://openviking.test',
    userId: 'alice',
    ...overrides,
  }
}

describe('getSessionScopeKey', () => {
  it('uses the user API key as the session data scope when both keys exist', () => {
    const first = getSessionScopeKey(
      connection({ adminApiKey: 'admin-one', apiKey: 'user-key' }),
      'admin',
    )
    const second = getSessionScopeKey(
      connection({ adminApiKey: 'admin-two', apiKey: 'user-key' }),
      'admin',
    )

    expect(first.keySource).toBe('api')
    expect(second.keySource).toBe('api')
    expect(first.keyHash).toBe(second.keyHash)
  })

  it('falls back to the admin API key when no user API key is configured', () => {
    const scope = getSessionScopeKey(
      connection({ adminApiKey: 'admin-key', apiKey: '' }),
      'root',
    )

    expect(scope.keySource).toBe('admin')
    expect(scope.keyHash).not.toBe('none')
  })
})
