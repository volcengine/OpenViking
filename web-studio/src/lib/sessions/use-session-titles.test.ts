import { describe, expect, it } from 'vitest'

import { createSessionTitleStorageKey } from './use-session-titles'

describe('createSessionTitleStorageKey', () => {
  it('partitions session titles by active identity', () => {
    expect(createSessionTitleStorageKey('account-a\u0000alice')).not.toBe(
      createSessionTitleStorageKey('account-b\u0000alice'),
    )
  })

  it('does not expose unescaped identity separators in the storage key', () => {
    expect(createSessionTitleStorageKey('account-a\u0000alice')).not.toContain(
      '\u0000',
    )
  })
})
