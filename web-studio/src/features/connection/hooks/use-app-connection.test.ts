import { describe, expect, it, vi } from 'vitest'

import {
  createConnectionError,
  createConnectionHealthHeaders,
  createConnectionRoleProbeKey,
  createIdentityScopeKey,
  createManagementAccountConnection,
  resolveConnectionRoleProbeState,
  resolveInitialApiKey,
  resolveSwitchedIdentity,
  shouldRedirectToLoginOnApiError,
  synchronizeConnectionRuntime,
  synchronizeResolvedDataIdentity,
} from '#/features/connection/lib/connection-runtime'
import i18n from '#/i18n'
import { ovClient } from '#/shared/api/ov-client/index'

const acceptClientError = () => true

describe('createConnectionError', () => {
  it('reads the active locale when the asynchronous error is created', async () => {
    const originalLanguage = i18n.resolvedLanguage || i18n.language
    try {
      await i18n.changeLanguage('en')
      const english = createConnectionError('credentialMismatch').message

      await i18n.changeLanguage('zh-CN')
      const chinese = createConnectionError('credentialMismatch').message

      expect(chinese).toBe('所选凭证与目标账号和用户不匹配。')
      expect(chinese).not.toBe(english)
    } finally {
      await i18n.changeLanguage(originalLanguage)
    }
  })
})

describe('createConnectionRoleProbeKey', () => {
  it('matches the connection produced by an internal identity synchronization', () => {
    const connection = {
      accountId: 'default',
      adminApiKey: 'root-key',
      apiKey: 'selected-user-key',
      baseUrl: 'http://localhost:1933/',
      userId: 'default',
    }
    const synchronized = {
      ...connection,
      accountId: 'workspace-a',
      userId: 'alice',
    }

    expect(createConnectionRoleProbeKey(synchronized, 'api_key')).toBe(
      createConnectionRoleProbeKey(
        {
          ...connection,
          accountId: 'workspace-a',
          baseUrl: 'http://localhost:1933',
          userId: 'alice',
        },
        'api_key',
      ),
    )
  })

  it('changes when a credential or asserted identity changes', () => {
    const connection = {
      accountId: 'workspace-a',
      adminApiKey: 'root-key',
      apiKey: 'selected-user-key',
      baseUrl: 'http://localhost:1933',
      userId: 'alice',
    }
    const key = createConnectionRoleProbeKey(connection, 'api_key')

    expect(
      createConnectionRoleProbeKey(
        { ...connection, apiKey: 'another-user-key' },
        'api_key',
      ),
    ).not.toBe(key)
    expect(
      createConnectionRoleProbeKey({ ...connection, userId: 'bob' }, 'api_key'),
    ).not.toBe(key)
  })
})

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

it('preserves a root login key and caller identity when selecting a gateway account', () => {
  const connection = {
    accountId: 'account-a',
    userId: 'root',
    apiKey: 'root-login-key',
    adminApiKey: '',
    baseUrl: 'http://localhost:1935',
  }
  expect(
    createManagementAccountConnection(connection, ' account-b ', true),
  ).toEqual({
    ...connection,
    accountId: 'account-b',
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
      serviceSelection: 'auto',
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

  it('passes the selected identity to a keyless development service', () => {
    synchronizeConnectionRuntime(
      {
        serviceSelection: 'opensource',
        accountId: 'work-memory',
        userId: 'alice',
        apiKey: '',
        adminApiKey: '',
        baseUrl: 'http://127.0.0.1:1933',
      },
      'dev',
    )
    expect(ovClient.getConnection()).toMatchObject({
      accountId: 'work-memory',
      userId: 'alice',
      identityHeaders: true,
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

  it.each(['api_key', 'trusted'] as const)(
    'keeps non-dev no-key connections unknown without probing: %s',
    (serverMode) => {
      expect(
        resolveConnectionRoleProbeState({
          apiKey: '',
          baseUrl: 'http://localhost:3000',
          serverMode,
        }),
      ).toEqual({
        isLoading: false,
        role: 'unknown',
        shouldProbe: false,
      })
    },
  )

  it.each(['api_key', 'trusted'] as const)(
    'probes non-dev keyed connections through /health: %s',
    (serverMode) => {
      expect(
        resolveConnectionRoleProbeState({
          apiKey: 'root-key',
          baseUrl: 'http://localhost:3000',
          serverMode,
        }),
      ).toEqual({
        isLoading: true,
        role: 'unknown',
        shouldProbe: true,
      })
    },
  )
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

describe('connection credential planes', () => {
  it('uses the hosted data credential even when a source control credential remains configured', () => {
    const connection = {
      baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
      apiKey: 'user-key',
      adminApiKey: 'expired-control-key',
      accountId: 'old-account',
      userId: 'old-user',
    }
    expect(createConnectionHealthHeaders(connection, 'data')).toEqual({
      Authorization: 'Bearer user-key',
    })
    expect(createConnectionHealthHeaders(connection, 'control')).toEqual({
      Authorization: 'Bearer user-key',
    })
    expect(
      createConnectionHealthHeaders({ ...connection, apiKey: '' }, 'data'),
    ).toEqual({})
  })
  it('drops identity assertions when a custom domain is explicitly selected as hosted', () => {
    const connection = {
      baseUrl: 'https://proxy.example/openviking',
      serviceSelection: 'volcengine' as const,
      apiKey: 'user-key',
      adminApiKey: '',
      accountId: 'old-account',
      userId: 'old-user',
    }
    expect(createConnectionHealthHeaders(connection, 'data')).toEqual({
      Authorization: 'Bearer user-key',
    })
    expect(
      createConnectionHealthHeaders(
        { ...connection, serviceSelection: 'opensource' },
        'data',
      ),
    ).toMatchObject({
      'X-OpenViking-Account': 'old-account',
      'X-OpenViking-User': 'old-user',
    })
  })
})

it('does not synchronize a control-key placeholder into the selected data user', () => {
  expect(
    synchronizeResolvedDataIdentity(
      {
        serviceSelection: 'opensource',
        baseUrl: 'https://service.test',
        apiKey: '',
        adminApiKey: 'admin-key',
        accountId: 'tenant',
        userId: 'alice',
      },
      { role: 'admin', accountId: 'tenant', userId: 'default' },
    ),
  ).toBeNull()
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))
