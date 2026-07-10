import { describe, expect, it } from 'vitest'

import {
  resolveConnectionRoleProbeState,
  resolveInitialApiKey,
  shouldRedirectToLoginOnApiError,
} from './use-app-connection'

const acceptClientError = () => true

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

describe('shouldRedirectToLoginOnApiError', () => {
  it('redirects on HTTP 401 session failures', () => {
    expect(
      shouldRedirectToLoginOnApiError(
        { statusCode: 401, code: 'UNAUTHENTICATED' },
        acceptClientError,
      ),
    ).toBe(true)
  })

  it('does not redirect on HTTP 403 business permission errors', () => {
    expect(
      shouldRedirectToLoginOnApiError(
        {
          statusCode: 403,
          code: 'PERMISSION_DENIED',
          details: { feishu_code: 1770032 },
        },
        acceptClientError,
      ),
    ).toBe(false)
  })

  it('does not redirect on other HTTP 403 permission denials', () => {
    expect(
      shouldRedirectToLoginOnApiError(
        { statusCode: 403, code: 'PERMISSION_DENIED' },
        acceptClientError,
      ),
    ).toBe(false)
  })
})
