import axios, { AxiosHeaders } from 'axios'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createAdminClient, fetchAdminUsersPage } from './admin'
import { configureManagementCredentials } from './management-credentials'
import { createOvClient } from './ov-client/client'

const connection = {
  baseUrl: 'http://service.test',
  accountId: 'team',
  userId: 'operator',
  apiKey: 'root-key',
}
const requests: InternalAxiosRequestConfig[] = []
const resolveAccount = vi.fn()
const invalidateAccount = vi.fn()
let release: () => void
const originalAdapter = axios.defaults.adapter
const adapter: AxiosAdapter = async (config) => {
  requests.push(config)
  return {
    config,
    status: 200,
    statusText: 'OK',
    headers: {},
    data: { status: 'ok', result: {} },
  }
}
beforeEach(() => {
  requests.length = 0
  resolveAccount.mockReset().mockImplementation(async (accountId: string) => ({
    apiKey: `${accountId}-admin`,
    userId: 'admin',
    rootOnlyUserIds: ['global-reader'],
  }))
  invalidateAccount.mockReset()
  release = configureManagementCredentials({
    ...connection,
    rootApiKey: connection.apiKey,
    resolveAccount,
    invalidateAccount,
  })
  axios.defaults.adapter = adapter
})
afterEach(() => {
  release()
  axios.defaults.adapter = originalAdapter
})
function key(index = 0) {
  return AxiosHeaders.from(requests[index].headers).get('X-API-Key')
}

it.each([
  ['GET', 'users?include_credentials=false'],
  ['POST', 'users'],
  ['GET', 'users/alice'],
  ['PUT', 'users/alice/role'],
  ['POST', 'users/alice/key'],
  ['DELETE', 'users/alice'],
  ['GET', 'users/alice/settings'],
  ['PATCH', 'users/alice/settings'],
  ['GET', 'groups'],
  ['PUT', 'groups/dev/members'],
  ['GET', 'memory-templates'],
  ['PUT', 'memory-templates/profile'],
  ['GET', 'configuration'],
  ['PATCH', 'configuration'],
  ['PATCH', 'settings'],
])('uses the target space Admin key for %s %s', async (method, suffix) => {
  const client = createAdminClient(connection)
  await client.request({
    method: method as 'GET',
    url: `/api/v1/admin/accounts/other/${suffix}`,
    ...(method === 'PUT' ? { body: { role: 'admin' } } : {}),
  })
  expect(resolveAccount).toHaveBeenCalledWith('other')
  expect(key()).toBe('other-admin')
  expect(connection.apiKey).toBe('root-key')
})
it.each([
  ['GET', '/api/v1/admin/accounts'],
  ['POST', '/api/v1/admin/accounts'],
  ['DELETE', '/api/v1/admin/accounts/team'],
  ['GET', '/api/v1/admin/accounts/team'],
  ['GET', '/api/v1/admin/configuration'],
  ['PATCH', '/api/v1/admin/configuration'],
  ['POST', '/api/v1/admin/restart'],
  ['GET', '/api/v1/admin/accounts/team/bot/connections'],
  ['GET', '/api/v1/admin/bot/capabilities'],
  ['GET', '/gateway/admin/access'],
  ['GET', '/api/v1/admin/accounts/team/users'],
  ['GET', '/api/v1/admin/accounts/team/users?include_credentials=true'],
])('keeps Root for %s %s', async (method, url) => {
  await createAdminClient(connection).request({ method: method as 'GET', url })
  expect(key()).toBe('root-key')
  expect(resolveAccount).not.toHaveBeenCalled()
})
it('keeps Root for GLOBAL lifecycle operations', async () => {
  const client = createAdminClient(connection)
  await client.post({
    url: '/api/v1/admin/accounts/team/users',
    body: { user_id: 'new-global', role: 'global' },
  })
  await client.post({
    url: '/api/v1/admin/accounts/team/users/global-reader/key',
  })
  await client.delete({
    url: '/api/v1/admin/accounts/team/users/global-reader',
  })
  expect(requests.map((_request, index) => key(index))).toEqual([
    'root-key',
    'root-key',
    'root-key',
  ])
})
it('uses Root for explicit account management when the space has no usable Admin credential', async () => {
  resolveAccount.mockResolvedValue(null)
  await createAdminClient(connection).get({
    url: '/api/v1/admin/accounts/team/groups',
  })
  expect(key()).toBe('root-key')
})
it('preserves a directly configured Account Admin credential', async () => {
  await createAdminClient({ ...connection, apiKey: 'own-admin' }).get({
    url: '/api/v1/admin/accounts/team/groups',
  })
  expect(key()).toBe('own-admin')
  expect(resolveAccount).not.toHaveBeenCalled()
})
it('routes the generated shared client through the same credential policy', async () => {
  const client = createOvClient({
    baseUrl: connection.baseUrl,
    credentialScope: 'control',
    connection: { adminApiKey: 'root-key' },
  })
  await client.client.patch({
    url: '/api/v1/admin/accounts/team/settings',
    body: {},
  })
  expect(key()).toBe('team-admin')
})
it('separates member metadata from Root-only credential disclosure', async () => {
  axios.defaults.adapter = async (config) => {
    requests.push(config)
    const metadata =
      new URL(config.url!, connection.baseUrl).searchParams.get(
        'include_credentials',
      ) === 'false'
    return {
      config,
      status: 200,
      statusText: 'OK',
      headers: {},
      data: {
        status: 'ok',
        result: {
          users: [
            {
              user_id: 'alice',
              role: 'user',
              ...(metadata ? {} : { api_key: 'alice-key' }),
            },
          ],
          total: 1,
          account_total: 5,
          manager_count: 2,
          key_count: 5,
        },
      },
    }
  }
  const page = await fetchAdminUsersPage(connection, 'team', {
    page: 1,
    pageSize: 20,
    search: '',
  })
  expect(requests.map((_request, index) => key(index))).toEqual([
    'team-admin',
    'root-key',
  ])
  expect(page.users[0].apiKey).toBe('alice-key')
  expect(page.accountTotal).toBe(5)
})
it('invalidates discovered credentials after changing user roles or keys', async () => {
  await createAdminClient(connection).post({
    url: '/api/v1/admin/accounts/team/users/alice/key',
  })
  expect(invalidateAccount).toHaveBeenCalledWith('team')
})
it('propagates a forbidden Admin operation without replaying it under Root', async () => {
  axios.defaults.adapter = async (config) => {
    requests.push(config)
    throw new axios.AxiosError(
      'Forbidden',
      'ERR_BAD_REQUEST',
      config,
      undefined,
      { config, status: 403, statusText: 'Forbidden', headers: {}, data: {} },
    )
  }
  await expect(
    createAdminClient(connection).delete({
      url: '/api/v1/admin/accounts/team/users/alice',
    }),
  ).rejects.toBeDefined()
  expect(requests).toHaveLength(1)
  expect(key()).toBe('team-admin')
  expect(invalidateAccount).toHaveBeenCalledWith('team')
})
it('stops routing when the Root connection is released', async () => {
  release()
  await createAdminClient(connection).get({
    url: '/api/v1/admin/accounts/team/groups',
  })
  expect(key()).toBe('root-key')
  expect(resolveAccount).not.toHaveBeenCalled()
})

it.each(['overview', 'logs', 'tools', 'upstreams', 'policies', 'keys'])(
  'uses Admin for gateway %s in the selected account',
  async (resource) => {
    await createAdminClient(connection).get({
      url: `/gateway/admin/${resource}`,
      query: { account_id: 'team' },
    })
    expect(key()).toBe('team-admin')
  },
)
it('preserves gateway management authorization while selecting the Account Admin credential', async () => {
  await createAdminClient(connection).post({
    url: '/gateway/admin/upstreams/import-vlm',
    query: { account_id: 'team' },
    headers: { 'X-OpenViking-Gateway-Management-Key': 'gateway-manager' },
  })
  expect(key()).toBe('team-admin')
  expect(requests[0].headers.get('X-OpenViking-Gateway-Management-Key')).toBe(
    'gateway-manager',
  )
})
it.each(['access', 'upstreams/import-vlm', 'account/data'])(
  'keeps Root for gateway %s without a separate management key',
  async (resource) => {
    await createAdminClient(connection).request({
      method: 'POST',
      url: `/gateway/admin/${resource}`,
      query: { account_id: 'team' },
    })
    expect(key()).toBe('root-key')
  },
)
it('uses Root when issuing a gateway key requires reading another user credential', async () => {
  await createAdminClient(connection).post({
    url: '/gateway/admin/keys',
    query: { account_id: 'team' },
    body: { user_id: 'alice' },
  })
  expect(key()).toBe('root-key')
})
