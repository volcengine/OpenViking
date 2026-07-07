import { describe, expect, it } from 'vitest'

import {
  resolveConnectionRoleProbeState,
  resolveInitialApiKey,
} from './use-app-connection'

describe('resolveInitialApiKey', () => {
  it('keeps the stored connection key paired with the stored account and user', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: '',
        storedApiKey: 'stored-selected-user-key',
      }),
    ).toBe('stored-selected-user-key')
  })

  it('falls back to the default key when no connection key is stored', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: '',
        storedApiKey: undefined,
      }),
    ).toBe('default-key')
  })

  it('honors an explicit environment key first', () => {
    expect(
      resolveInitialApiKey({
        defaultApiKey: 'default-key',
        envApiKey: 'env-key',
        storedApiKey: 'stored-selected-user-key',
      }),
    ).toBe('env-key')
  })
})

describe('resolveConnectionRoleProbeState', () => {
  it('treats dev mode as root without requiring an API key', () => {
    expect(
      resolveConnectionRoleProbeState({
        apiKey: '',
        baseUrl: 'http://localhost:3000',
        serverMode: 'dev',
      }),
    ).toEqual({
      isLoading: false,
      role: 'root',
      shouldProbe: false,
    })
  })

  it('keeps non-dev no-key connections unknown without probing', () => {
    expect(
      resolveConnectionRoleProbeState({
        apiKey: '',
        baseUrl: 'http://localhost:3000',
        serverMode: 'api_key',
      }),
    ).toEqual({
      isLoading: false,
      role: 'unknown',
      shouldProbe: false,
    })
  })

  it('probes non-dev keyed connections through /health', () => {
    expect(
      resolveConnectionRoleProbeState({
        apiKey: 'root-key',
        baseUrl: 'http://localhost:3000',
        serverMode: 'api_key',
      }),
    ).toEqual({
      isLoading: true,
      role: 'unknown',
      shouldProbe: true,
    })
  })
})
