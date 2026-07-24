import { describe, expect, it } from 'vitest'

import { selectAccountUser } from './account-switcher'
import type { AdminUser } from '#/lib/admin'

const users: AdminUser[] = [
  {
    accountId: 'workspace',
    apiKey: 'alice-key',
    role: 'user',
    userId: 'alice',
  },
  {
    accountId: 'workspace',
    apiKey: 'default-key',
    role: 'admin',
    userId: 'default',
  },
]

describe('selectAccountUser', () => {
  it('selects the first user returned by the target account', () => {
    expect(selectAccountUser(users, 'default', true)?.userId).toBe('alice')
  })

  it('falls back to the first user for a new account', () => {
    expect(selectAccountUser(users, 'missing', true)?.userId).toBe('alice')
  })

  it('rejects prefix-only users in API-key mode', () => {
    expect(
      selectAccountUser(
        [
          {
            accountId: 'workspace',
            keyPrefix: 'ovk_123',
            role: 'admin',
            userId: 'default',
          },
        ],
        'default',
        true,
      ),
    ).toBeUndefined()
  })
})
