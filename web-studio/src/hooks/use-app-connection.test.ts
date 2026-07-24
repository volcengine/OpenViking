import { describe, expect, it } from 'vitest'

import {
  createManagementAccountConnection,
  createIdentityScopeKey,
  resolveSwitchedIdentity,
  resolveConnectionRoleProbeState,
  resolveInitialApiKey,
  shouldRedirectToLoginOnApiError,
  synchronizeConnectionRuntime,
  synchronizeResolvedDataIdentity,
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

describe('synchronizeResolvedDataIdentity', () => {
  it('updates the active account and user even when a Root control key is configured', () => {
    const connection = {
      accountId: 'account-a',
      adminApiKey: 'root-key',
      apiKey: 'account-b-user-key',
      baseUrl: 'http://localhost:1933',
      userId: 'alice',
    }

    expect(
      synchronizeResolvedDataIdentity(connection, {
        accountId: 'account-b',
        role: 'user',
        userId: 'bob',
      }),
    ).toEqual({
      ...connection,
      accountId: 'account-b',
      userId: 'bob',
    })
  })

  it('does not rewrite an already synchronized identity', () => {
    const connection = {
      accountId: 'account-b',
      adminApiKey: 'root-key',
      apiKey: 'account-b-user-key',
      baseUrl: 'http://localhost:1933',
      userId: 'bob',
    }

    expect(
      synchronizeResolvedDataIdentity(connection, {
        accountId: 'account-b',
        role: 'user',
        userId: 'bob',
      }),
    ).toBeNull()
  })
})

describe('resolveSwitchedIdentity', () => {
  it('uses the requested account and user for legacy health responses', () => {
    expect(
      resolveSwitchedIdentity(
        { accountId: 'account-b', userId: 'bob' },
        { accountId: '', role: 'user', userId: '' },
      ),
    ).toEqual({ accountId: 'account-b', userId: 'bob' })
  })

  it('rejects a credential that resolves to another identity', () => {
    expect(
      resolveSwitchedIdentity(
        { accountId: 'account-b', userId: 'bob' },
        { accountId: 'account-a', role: 'user', userId: 'alice' },
      ),
    ).toBeNull()
  })

  it('uses an Admin API credential association with legacy health endpoints', () => {
    expect(
      resolveSwitchedIdentity(
        { accountId: 'account-b', userId: 'bob' },
        { accountId: '', role: 'unknown', userId: '' },
        true,
      ),
    ).toEqual({ accountId: 'account-b', userId: 'bob' })
  })
})

describe('createManagementAccountConnection', () => {
  it('keeps the Root credential while clearing stale tenant data credentials', () => {
    expect(
      createManagementAccountConnection(
        {
          accountId: 'account-a',
          adminApiKey: 'root-key',
          apiKey: 'account-a-user-key',
          baseUrl: 'http://localhost:1933',
          userId: 'alice',
        },
        ' account-b ',
      ),
    ).toEqual({
      accountId: 'account-b',
      adminApiKey: 'root-key',
      apiKey: '',
      baseUrl: 'http://localhost:1933',
      userId: '',
    })
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
