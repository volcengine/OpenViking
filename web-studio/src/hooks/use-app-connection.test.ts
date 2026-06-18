import { describe, expect, it } from 'vitest'

import { resolveInitialApiKey } from './use-app-connection'

describe('resolveInitialApiKey', () => {
  it('keeps the stored connection key paired with the stored account and user', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: '',
        sessionApiKey: 'previous-session-user-key',
        storedApiKey: 'stored-selected-user-key',
      }),
    ).toBe('stored-selected-user-key')
  })

  it('falls back to the session key when no connection key is stored', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: '',
        sessionApiKey: 'session-user-key',
        storedApiKey: undefined,
      }),
    ).toBe('session-user-key')
  })

  it('honors an explicit environment key first', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: 'env-key',
        sessionApiKey: 'session-user-key',
        storedApiKey: 'stored-selected-user-key',
      }),
    ).toBe('env-key')
  })
})
