import { describe, expect, it } from 'vitest'

import {
  createIdentityScopeKey,
  resolveConnectionRoleProbeState,
  resolveInitialApiKey,
  shouldRedirectToLoginOnApiError,
  synchronizeConnectionRuntime,
} from './use-app-connection'
import { ovClient } from '#/lib/ov-client'

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

describe('createIdentityScopeKey', () => {
  it('changes when the active account changes', () => {
    const connection = {
      accountId: 'account-a',
      adminApiKey: 'root-key',
      apiKey: 'user-key',
      baseUrl: 'http://localhost:1933',
      userId: 'default',
    }

    expect(createIdentityScopeKey(connection, 'api_key')).not.toBe(
      createIdentityScopeKey(
        {
          ...connection,
          accountId: 'account-b',
        },
        'api_key',
      ),
    )
  })

  it('does not expose the raw data credential', () => {
    const scope = createIdentityScopeKey(
      {
        accountId: 'default',
        adminApiKey: 'root-key',
        apiKey: 'secret-user-key',
        baseUrl: 'http://localhost:1933',
        userId: 'default',
      },
      'api_key',
    )

    expect(scope).not.toContain('secret-user-key')
  })
})

describe('synchronizeConnectionRuntime', () => {
  it('updates the imperative client before React state consumers remount', () => {
    const next = synchronizeConnectionRuntime(
      {
        accountId: 'account-b',
        adminApiKey: 'root-key',
        apiKey: 'account-b-user-key',
        baseUrl: 'http://localhost:1933/',
        userId: 'bob',
      },
      'api_key',
    )

    expect(next).toEqual({
      accountId: 'account-b',
      adminApiKey: 'root-key',
      apiKey: 'account-b-user-key',
      baseUrl: 'http://localhost:1933/',
      userId: 'bob',
    })
    expect(ovClient.getConnection()).toMatchObject({
      accountId: 'account-b',
      adminApiKey: 'root-key',
      apiKey: 'account-b-user-key',
      identityHeaders: false,
      userId: 'bob',
    })
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
