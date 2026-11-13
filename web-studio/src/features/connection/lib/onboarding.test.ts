// @vitest-environment jsdom
import {
  completeStudioSetup,
  needsStudioSetup,
  SETUP_STORAGE_KEY,
  studioSetupDestination,
  StudioSetupRequestError,
  verifyStudioSetup,
  WORKSPACE_SESSION_KEY,
} from '#/features/connection/lib/onboarding'
import { PROFILES_STORAGE_KEY } from '#/features/connection/lib/profiles'
import type * as ClientModule from '#/gen/ov-client/client'
import { createClient } from '#/gen/ov-client/client'
import { OvClientError } from '#/shared/api/ov-client/index'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('#/gen/ov-client/client', async (original) => {
  const actual = await original<typeof ClientModule>()
  return {
    ...actual,
    createClient: vi.fn(
      (options?: Parameters<typeof actual.createClient>[0]) => ({
        ...actual.createClient(options),
        get,
      }),
    ),
  }
})
const draft = {
  serviceSelection: 'opensource' as const,
  baseUrl: 'http://service.test',
  apiKey: 'synthetic-key',
  adminApiKey: '',
  accountId: 'default',
  userId: 'default',
}
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  get.mockReset()
  vi.mocked(createClient).mockClear()
})
afterEach(() => vi.unstubAllGlobals())
describe('first-use detection', () => {
  it('starts setup for a new tab and retains unfinished setup on reload', async () => {
    expect(needsStudioSetup()).toBe(true)
    localStorage.setItem(SETUP_STORAGE_KEY, 'pending')
    localStorage.setItem(
      PROFILES_STORAGE_KEY,
      JSON.stringify({ profiles: [{ connection: draft }] }),
    )
    expect(needsStudioSetup()).toBe(true)
  })
  it('restores a legacy connection even after the upgrade marked setup pending', async () => {
    localStorage.setItem(SETUP_STORAGE_KEY, 'pending')
    localStorage.setItem('ov_console_connection', JSON.stringify(draft))
    expect(needsStudioSetup()).toBe(false)
  })
  it('restores saved profiles in a new tab without a session marker', async () => {
    localStorage.setItem(
      PROFILES_STORAGE_KEY,
      JSON.stringify({
        activeId: 'saved',
        profiles: [{ id: 'saved', name: 'Saved', connection: draft }],
      }),
    )
    expect(needsStudioSetup()).toBe(false)
  })
  it('does not treat an automatically persisted empty same-origin connection as configured', async () => {
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({ ...draft, baseUrl: window.location.origin, apiKey: '' }),
    )
    expect(needsStudioSetup()).toBe(true)
  })
  it('completes a verified development connection without a key', async () => {
    await completeStudioSetup(
      { ...draft, baseUrl: window.location.origin, apiKey: '' },
      'Development',
    )
    expect(needsStudioSetup()).toBe(false)
    expect(sessionStorage.getItem(WORKSPACE_SESSION_KEY)).toBe('selected')
    sessionStorage.clear()
    expect(needsStudioSetup()).toBe(false)
    localStorage.removeItem(PROFILES_STORAGE_KEY)
    expect(needsStudioSetup()).toBe(false)
    localStorage.removeItem('ov_console_connection')
    expect(needsStudioSetup()).toBe(true)
  })
  it('completes setup when randomUUID is unavailable on an HTTP origin', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
    })
    await completeStudioSetup(draft, 'OpenViking')
    const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY) || '{}')
    expect(saved.profiles).toHaveLength(1)
    expect(saved.profiles[0].id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
    expect(saved.activeId).toBe(saved.profiles[0].id)
    expect(needsStudioSetup()).toBe(false)
  })
  it('retains other saved workspaces when selecting a new connection', async () => {
    localStorage.setItem(
      PROFILES_STORAGE_KEY,
      JSON.stringify({
        activeId: 'one',
        profiles: [{ id: 'one', name: 'Original', connection: draft }],
      }),
    )
    await completeStudioSetup(
      {
        ...draft,
        serviceSelection: 'volcengine',
        baseUrl: 'https://hosted.test',
      },
      'Volcengine',
    )
    const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY) || '{}')
    expect(saved.profiles).toHaveLength(2)
    expect(saved.profiles[0].name).toBe('Original')
    expect(saved.profiles[1].connection.serviceSelection).toBe('volcengine')
  })
  it('keeps the selected profile when saved profiles share an address', async () => {
    localStorage.setItem(
      PROFILES_STORAGE_KEY,
      JSON.stringify({
        activeId: 'second',
        profiles: [
          { id: 'first', name: 'First', connection: draft },
          { id: 'second', name: 'Second', connection: draft },
        ],
      }),
    )
    await completeStudioSetup(
      { ...draft, apiKey: 'updated-key' },
      'OpenViking',
      'second',
    )
    const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY) || '{}')
    expect(saved.activeId).toBe('second')
    expect(saved.profiles[0].connection.apiKey).toBe('synthetic-key')
    expect(saved.profiles[1].connection.apiKey).toBe('updated-key')
  })
  it('keeps both workspaces when a new connection uses an existing service address', async () => {
    const hosted = {
      ...draft,
      serviceSelection: 'volcengine' as const,
      baseUrl: 'https://hosted.test',
    }
    await completeStudioSetup({ ...hosted, apiKey: 'alice-key' }, 'Alice')
    const first = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY) || '{}')

    await completeStudioSetup({ ...hosted, apiKey: 'bob-key' }, 'Bob')
    const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY) || '{}')
    expect(saved.profiles).toHaveLength(2)
    expect(saved.profiles[0]).toEqual(first.profiles[0])
    expect(saved.profiles[1]).toMatchObject({
      name: 'Bob',
      connection: { apiKey: 'bob-key' },
    })
    expect(saved.activeId).toBe(saved.profiles[1].id)
    expect(saved.activeId).not.toBe(first.activeId)
  })
})
describe('verification', () => {
  it('identifies the failed verification stage and masks credentials', async () => {
    const error = new StudioSetupRequestError(
      'health',
      new OvClientError({
        code: 'PaymentRequired',
        message: 'Account check failed for synthetic-key',
        statusCode: 402,
      }),
      draft,
    )
    expect(error.stage).toBe('health')
    expect(error.statusCode).toBe(402)
    expect(error.message).toContain('HTTP 402')
    expect(error.message).not.toContain('synthetic-key')
  })
  it('uses hosted Bearer credentials and service-returned identity before checking private data', async () => {
    get
      .mockResolvedValueOnce({
        data: {
          status: 'ok',
          version: 'cloud',
          auth_mode: 'api_key',
          account_id: 'tenant',
          user_id: 'alice',
        },
      })
      .mockResolvedValueOnce({
        status: 200,
        headers: {},
        data: { status: 'ok', result: [] },
      })
    const c = await verifyStudioSetup(
      {
        ...draft,
        serviceSelection: 'volcengine',
        adminApiKey: 'never-send-control',
      },
      new AbortController().signal,
    )
    expect(c.userId).toBe('alice')
    expect(c.adminApiKey).toBe('')
    expect(vi.mocked(createClient).mock.calls[0][0]?.headers).toEqual({
      Authorization: 'Bearer synthetic-key',
    })
    expect(get.mock.calls[1][0].query.uri).toBe('viking://~/')
  })
  it('does not invent a hosted identity when health omits it', async () => {
    get
      .mockResolvedValueOnce({
        data: { version: 'test', auth_mode: 'api_key' },
      })
      .mockResolvedValueOnce({
        status: 200,
        headers: {},
        data: { status: 'ok', result: [] },
      })
    const connection = await verifyStudioSetup(
      { ...draft, serviceSelection: 'volcengine' },
      new AbortController().signal,
    )
    expect(connection.accountId).toBe('')
    expect(connection.userId).toBe('')
  })
  it('rejects HTML health responses instead of accepting the Studio URL', async () => {
    get.mockResolvedValueOnce({ data: '<html></html>' })
    await expect(
      verifyStudioSetup(draft, new AbortController().signal),
    ).rejects.toThrow('invalidService')
    expect(get).toHaveBeenCalledTimes(1)
  })
  it('does not accept a health-only response when data access fails', async () => {
    get
      .mockResolvedValueOnce({
        data: { version: 'test', auth_mode: 'api_key' },
      })
      .mockRejectedValueOnce(new Error('Forbidden'))
    await expect(
      verifyStudioSetup(draft, new AbortController().signal),
    ).rejects.toMatchObject({ stage: 'data', message: 'Forbidden' })
    expect(localStorage.getItem(PROFILES_STORAGE_KEY)).toBeNull()
  })
  it('requires a key except in development mode', async () => {
    get.mockResolvedValueOnce({
      data: { version: 'test', auth_mode: 'api_key' },
    })
    await expect(
      verifyStudioSetup({ ...draft, apiKey: '' }, new AbortController().signal),
    ).rejects.toThrow('keyRequired')
  })
})

describe('management bootstrap', () => {
  it.each(['data', 'control'])(
    'verifies trusted tenant access using a Root key entered as %s without requiring a User Key',
    async (field) => {
      get
        .mockResolvedValueOnce({
          data: {
            version: 'test',
            auth_mode: 'trusted',
            role: 'user',
            account_id: 'team',
            user_id: 'alice',
          },
        })
        .mockResolvedValueOnce({
          status: 200,
          headers: {},
          data: { status: 'ok', result: [] },
        })
      const connection = await verifyStudioSetup(
        {
          ...draft,
          apiKey: field === 'data' ? 'trusted-root' : '',
          adminApiKey: field === 'control' ? 'trusted-root' : '',
          accountId: 'team',
          userId: 'alice',
        },
        new AbortController().signal,
      )
      expect(connection).toMatchObject({
        apiKey: '',
        adminApiKey: 'trusted-root',
        accountId: 'team',
        userId: 'alice',
      })
      expect(get.mock.calls[1][0].url).toBe('/api/v1/fs/ls')
      expect(
        vi.mocked(createClient).mock.calls.at(-1)?.[0]?.headers,
      ).toMatchObject({
        'X-API-Key': 'trusted-root',
        'X-OpenViking-Account': 'team',
        'X-OpenViking-User': 'alice',
      })
    },
  )
  it.each(['root', 'admin'])(
    'recognizes %s capabilities regardless of the credential input field',
    async (role) => {
      get
        .mockResolvedValueOnce({
          data: {
            version: 'test',
            auth_mode: 'api_key',
            role,
            account_id: 'tenant',
            user_id: 'admin',
          },
        })
        .mockResolvedValueOnce({
          status: 200,
          headers: {},
          data: { status: 'ok', result: [] },
        })
      const connection = await verifyStudioSetup(
        { ...draft, apiKey: '', adminApiKey: 'synthetic-control' },
        new AbortController().signal,
      )
      expect(get.mock.calls[1][0].url).toBe(
        role === 'root' ? '/api/v1/admin/accounts' : '/api/v1/fs/ls',
      )
      expect(studioSetupDestination(connection)).toBe(
        role === 'root' ? '/admin' : '/home',
      )
      expect(
        get.mock.calls.some(([options]) => options.url === '/api/v1/fs/ls'),
      ).toBe(role === 'admin')
    },
  )
  it('rejects denied management access and does not save it', async () => {
    get
      .mockResolvedValueOnce({
        data: { version: 'test', auth_mode: 'api_key', role: 'root' },
      })
      .mockRejectedValueOnce(new Error('Forbidden'))
    await expect(
      verifyStudioSetup(
        { ...draft, apiKey: '', adminApiKey: 'synthetic-control' },
        new AbortController().signal,
      ),
    ).rejects.toThrow('Forbidden')
    expect(localStorage.getItem(PROFILES_STORAGE_KEY)).toBeNull()
  })
  it('does not use a hosted control credential as a user credential', async () => {
    get.mockResolvedValueOnce({
      data: { version: 'test', auth_mode: 'api_key', role: 'root' },
    })
    await expect(
      verifyStudioSetup(
        {
          ...draft,
          serviceSelection: 'volcengine',
          apiKey: '',
          adminApiKey: 'synthetic-control',
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow('keyRequired')
    expect(get).toHaveBeenCalledTimes(1)
  })
  it('keeps data connections on their normal landing page', async () => {
    expect(studioSetupDestination(draft)).toBe('/home')
    expect(
      studioSetupDestination({ ...draft, serviceSelection: 'volcengine' }),
    ).toBe('/filesystem')
  })
})

it.each([
  ['root', 'chosen-account'],
  ['admin', 'bound-account'],
])(
  'binds a standalone %s key to the appropriate data or management identity',
  async (role, expectedAccount) => {
    get
      .mockResolvedValueOnce({
        data: {
          version: 'test',
          auth_mode: 'api_key',
          role,
          account_id: role === 'root' ? 'default' : 'bound-account',
          user_id: 'default',
        },
      })
      .mockResolvedValueOnce({
        status: 200,
        headers: {},
        data: { status: 'ok', result: [] },
      })
    const result = await verifyStudioSetup(
      {
        ...draft,
        apiKey: '',
        adminApiKey: 'control-key',
        accountId: 'chosen-account',
        userId: 'alice',
      },
      new AbortController().signal,
    )
    expect(result.accountId).toBe(expectedAccount)
    expect(result.userId).toBe(role === 'root' ? 'alice' : 'default')
    expect(result.apiKey).toBe(role === 'root' ? '' : 'control-key')
    if (role === 'root')
      expect(get.mock.calls[1][0].path.account_id).toBe(expectedAccount)
  },
)

it('uses the User API Key identity when a separate control key is also configured', async () => {
  get
    .mockResolvedValueOnce({
      data: {
        version: 'test',
        auth_mode: 'api_key',
        role: 'user',
        account_id: 'user-account',
        user_id: 'alice',
      },
    })
    .mockResolvedValueOnce({
      status: 200,
      headers: {},
      data: { status: 'ok', result: [] },
    })
  const result = await verifyStudioSetup(
    { ...draft, adminApiKey: 'control-key' },
    new AbortController().signal,
  )
  expect(result.accountId).toBe('user-account')
  expect(result.userId).toBe('alice')
  expect(vi.mocked(createClient).mock.calls[0][0]?.headers?.['X-API-Key']).toBe(
    'synthetic-key',
  )
})

it('classifies a Root key entered in the ordinary key field before verifying access', async () => {
  get
    .mockResolvedValueOnce({
      data: { version: 'test', auth_mode: 'api_key', role: 'root' },
    })
    .mockResolvedValueOnce({
      status: 200,
      headers: {},
      data: { status: 'ok', result: [] },
    })
  const connection = await verifyStudioSetup(
    draft,
    new AbortController().signal,
  )
  expect(connection.apiKey).toBe('')
  expect(connection.adminApiKey).toBe('synthetic-key')
  expect(get.mock.calls[1][0].url).toBe('/api/v1/admin/accounts')
  expect(studioSetupDestination(connection)).toBe('/admin')
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))

it.each(['user', 'admin'])(
  'recognizes reversed Root + %s inputs without losing either credential',
  async (role) => {
    get
      .mockResolvedValueOnce({
        data: { version: 'test', auth_mode: 'api_key', role: 'root' },
      })
      .mockResolvedValueOnce({
        data: {
          version: 'test',
          auth_mode: 'api_key',
          role,
          account_id: 'team',
          user_id: 'alice',
        },
      })
      .mockResolvedValueOnce({
        status: 200,
        headers: {},
        data: { status: 'ok', result: [] },
      })
    const result = await verifyStudioSetup(
      { ...draft, apiKey: 'synthetic-root', adminApiKey: 'synthetic-user' },
      new AbortController().signal,
    )
    expect(result).toMatchObject({
      apiKey: 'synthetic-user',
      adminApiKey: 'synthetic-root',
      accountId: 'team',
      userId: 'alice',
    })
    expect(get.mock.calls[2][0].url).toBe('/api/v1/fs/ls')
    expect(studioSetupDestination(result)).toBe('/home')
  },
)

it('uses the credential that actually authenticated trusted mode when both fields are supplied', async () => {
  get
    .mockResolvedValueOnce({
      data: {
        version: 'test',
        auth_mode: 'trusted',
        role: 'user',
        account_id: 'team',
        user_id: 'alice',
      },
    })
    .mockResolvedValueOnce({
      status: 200,
      headers: {},
      data: { status: 'ok', result: [] },
    })
  const result = await verifyStudioSetup(
    { ...draft, apiKey: 'verified-root', adminApiKey: 'ordinary-user-key' },
    new AbortController().signal,
  )
  expect(result).toMatchObject({ apiKey: '', adminApiKey: 'verified-root' })
  expect(vi.mocked(createClient).mock.calls.at(-1)?.[0]?.headers).toMatchObject(
    { 'X-API-Key': 'verified-root' },
  )
})
it('recovers a stale User key only after confirming trusted mode and validating the separate Root key', async () => {
  get
    .mockRejectedValueOnce(
      new OvClientError({
        code: 'Unauthorized',
        statusCode: 401,
        message: 'invalid data credential',
      }),
    )
    .mockResolvedValueOnce({ data: { version: 'test', auth_mode: 'trusted' } })
    .mockResolvedValueOnce({
      data: {
        version: 'test',
        auth_mode: 'trusted',
        role: 'user',
        account_id: 'team',
        user_id: 'alice',
      },
    })
    .mockResolvedValueOnce({
      status: 200,
      headers: {},
      data: { status: 'ok', result: [] },
    })
  const result = await verifyStudioSetup(
    { ...draft, apiKey: 'stale-user', adminApiKey: 'root' },
    new AbortController().signal,
  )
  expect(result).toMatchObject({
    apiKey: '',
    adminApiKey: 'root',
    accountId: 'team',
    userId: 'alice',
  })
  expect(vi.mocked(createClient).mock.calls[1][0]?.headers).toEqual({})
})
it('does not retry an invalid data credential with Root in API Key mode', async () => {
  get
    .mockRejectedValueOnce(
      new OvClientError({
        code: 'Unauthorized',
        statusCode: 401,
        message: 'invalid data credential',
      }),
    )
    .mockResolvedValueOnce({ data: { version: 'test', auth_mode: 'api_key' } })
  await expect(
    verifyStudioSetup(
      { ...draft, apiKey: 'stale-user', adminApiKey: 'root' },
      new AbortController().signal,
    ),
  ).rejects.toMatchObject({ stage: 'health', statusCode: 401 })
  expect(get).toHaveBeenCalledTimes(2)
  expect(vi.mocked(createClient).mock.calls[1][0]?.headers).toEqual({})
})
