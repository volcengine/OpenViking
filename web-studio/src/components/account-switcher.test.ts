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
  it('keeps the same user id when it exists in the target account', () => {
    expect(selectAccountUser(users, 'alice', true)?.userId).toBe('alice')
  })

  it('falls back to the default user for a new account', () => {
    expect(selectAccountUser(users, 'missing', true)?.userId).toBe('default')
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
