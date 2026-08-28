import { describe, expect, it } from 'vitest'

import { canSwitchToManagedUser } from './identity-switch'

describe('canSwitchToManagedUser', () => {
  it('allows trusted mode to switch without a user API key', () => {
    expect(
      canSwitchToManagedUser({
        current: false,
        hasApiKey: false,
        serverMode: 'trusted',
      }),
    ).toBe(true)
  })

  it('requires a user API key in API-key mode', () => {
    expect(
      canSwitchToManagedUser({
        current: false,
        hasApiKey: false,
        serverMode: 'api_key',
      }),
    ).toBe(false)
  })

  it('does not offer switching for the current identity', () => {
    expect(
      canSwitchToManagedUser({
        current: true,
        hasApiKey: true,
        serverMode: 'trusted',
      }),
    ).toBe(false)
  })
})
