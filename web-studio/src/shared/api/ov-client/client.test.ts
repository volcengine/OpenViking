import type { AxiosRequestConfig } from 'axios'
import axios from 'axios'
import { describe, expect, it } from 'vitest'

import { createOvClient } from '#/shared/api/ov-client/client'

function readRequestHeader(config: AxiosRequestConfig, name: string): string {
  const headers = config.headers as
    | { get?: (headerName: string) => unknown }
    | Record<string, unknown>
    | undefined
  if (!headers) {
    return ''
  }
  if ('get' in headers && typeof headers.get === 'function') {
    const value = headers.get(name)
    return typeof value === 'string' ? value : ''
  }
  const value = headers[name] ?? headers[name.toLowerCase()]
  return typeof value === 'string' ? value : ''
}

function createRecordingClient(baseUrl = 'http://openviking.test') {
  const requests: AxiosRequestConfig[] = []
  const instance = axios.create({
    adapter: async (config) => {
      requests.push(config)
      return {
        config,
        data: { result: {}, status: 'ok' },
        headers: {},
        status: 200,
        statusText: 'OK',
      }
    },
  })
  const client = createOvClient({
    axios: instance,
    baseUrl,
    bindSdkClient: false,
  })
  return { client, requests }
}

it('uses Root for trusted data assertions only while preserving data-scope admin blocking', async () => {
  const { client, requests } = createRecordingClient()
  client.setOptions({ credentialScope: 'data' })
  client.setConnection({
    trustedAuth: true,
    identityHeaders: true,
    adminApiKey: 'trusted-root',
    accountId: 'team',
    userId: 'bob',
  })
  await client.instance.get('/api/v1/fs/ls')
  expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('trusted-root')
  expect(readRequestHeader(requests[0], 'X-OpenViking-User')).toBe('bob')
  await expect(
    client.instance.get('/api/v1/admin/accounts'),
  ).rejects.toMatchObject({ statusCode: 403 })
  expect(requests).toHaveLength(1)
  client.setOptions({ credentialScope: 'control' })
  await client.instance.get('/api/v1/admin/accounts')
  expect(readRequestHeader(requests[1], 'X-OpenViking-Account')).toBe('')
  expect(readRequestHeader(requests[1], 'X-OpenViking-User')).toBe('')
  client.setOptions({ credentialScope: 'data' })
  client.setConnection({
    trustedAuth: false,
    identityHeaders: false,
    adminApiKey: '',
    apiKey: 'user-key',
  })
  await client.instance.get('/api/v1/fs/ls')
  expect(readRequestHeader(requests[2], 'X-API-Key')).toBe('user-key')
  expect(readRequestHeader(requests[2], 'X-OpenViking-User')).toBe('')
})

it('uses the documented hosted Bearer credential and returns to source headers after switching', async () => {
  const { client, requests } = createRecordingClient(
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
  )
  client.setConnection({ apiKey: 'user-key' })
  await client.instance.get('/api/v1/fs/ls')
  expect(readRequestHeader(requests[0], 'Authorization')).toBe(
    'Bearer user-key',
  )
  expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('')
  client.setOptions({
    baseUrl: 'https://proxy.example/openviking',
    apiKeyAuth: 'bearer',
  })
  await client.instance.get('/health', {
    headers: { Authorization: 'Bearer candidate-key' },
  })
  expect(readRequestHeader(requests[1], 'Authorization')).toBe(
    'Bearer candidate-key',
  )
  client.setOptions({ baseUrl: 'http://localhost:19340' })
  await client.instance.get('/api/v1/fs/ls')
  expect(readRequestHeader(requests[2], 'X-API-Key')).toBe('user-key')
  expect(readRequestHeader(requests[2], 'Authorization')).toBe('')
  client.setOptions({
    baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
  })
  client.setConnection({ apiKey: '', adminApiKey: 'source-control-key' })
  await client.instance.get('/api/v1/fs/ls')
  expect(readRequestHeader(requests[3], 'Authorization')).toBe('')
  expect(readRequestHeader(requests[3], 'X-API-Key')).toBe('')
})

describe('createOvClient API key selection', () => {
  it('uses the data API key for dashboard metrics when both keys are configured', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      adminApiKey: 'admin-key',
      apiKey: 'user-key',
    })

    await client.instance.get('/api/v1/console/dashboard/summary')
    await client.instance.get('/api/v1/console/tokens')
    await client.instance.get('/api/v1/console/context-commits')

    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('user-key')
    expect(readRequestHeader(requests[1], 'X-API-Key')).toBe('user-key')
    expect(readRequestHeader(requests[2], 'X-API-Key')).toBe('user-key')
  })

  it('uses the data API key for scoped audit logs when both keys are configured', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      adminApiKey: 'admin-key',
      apiKey: 'user-key',
    })

    await client.instance.get('/api/v1/console/audit')

    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('user-key')
  })

  it('keeps admin endpoints on the admin API key', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      adminApiKey: 'admin-key',
      apiKey: 'user-key',
    })

    await client.instance.get('/api/v1/admin/accounts')

    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('admin-key')
  })

  it('falls back to the admin API key for console data when no data key is configured', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      adminApiKey: 'admin-key',
      apiKey: '',
    })

    await client.instance.get('/api/v1/console/dashboard/summary')
    await client.instance.get('/api/v1/console/audit')

    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('admin-key')
    expect(readRequestHeader(requests[1], 'X-API-Key')).toBe('admin-key')
  })

  it('preserves an explicit API key used to probe a candidate identity', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      adminApiKey: 'root-key',
      apiKey: 'current-user-key',
    })

    await client.instance.get('/health', {
      headers: {
        'X-API-Key': 'candidate-user-key',
      },
    })

    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe(
      'candidate-user-key',
    )
  })

  it('preserves explicit trusted identity headers used to probe a candidate user', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({
      accountId: 'account-a',
      adminApiKey: 'root-key',
      identityHeaders: true,
      userId: 'alice',
    })

    await client.instance.get('/health', {
      headers: {
        'X-OpenViking-Account': 'account-a',
        'X-OpenViking-User': 'bob',
      },
    })

    expect(readRequestHeader(requests[0], 'X-OpenViking-Account')).toBe(
      'account-a',
    )
    expect(readRequestHeader(requests[0], 'X-OpenViking-User')).toBe('bob')
  })
})

it('uses the control credential for Studio bot management only', async () => {
  const { client, requests } = createRecordingClient()
  client.setConnection({ adminApiKey: 'admin-key', apiKey: 'user-key' })
  await client.instance.get('/api/v1/admin/accounts/team/bot/connections')
  await client.instance.post('/bot/v1/chat/stream')
  expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('admin-key')
  expect(readRequestHeader(requests[1], 'X-API-Key')).toBe('user-key')
})

it('scopes Studio root management to the selected account without asserting a data identity', async () => {
  const { client, requests } = createRecordingClient()
  client.setConnection({
    adminApiKey: 'root-key',
    apiKey: 'user-key',
    accountId: 'team',
    identityHeaders: false,
  })
  await client.instance.get('/api/v1/admin/accounts/team/bot/connections')
  await client.instance.get('/bot/v1/chat')
  expect(readRequestHeader(requests[0], 'X-OpenViking-Studio-Account')).toBe('')
  expect(readRequestHeader(requests[0], 'X-OpenViking-Account')).toBe('')
  expect(readRequestHeader(requests[1], 'X-OpenViking-Studio-Account')).toBe('')
})

it.each([
  '/api/v1/fs/ls',
  '/api/v1/sessions',
  '/api/v1/acl',
  '/api/v1/search/find',
  '/api/v1/tasks/task-a/cancel',
])(
  'never falls back to the Root credential for tenant operation %s',
  async (url) => {
    const { client, requests } = createRecordingClient()
    client.setConnection({ apiKey: '', adminApiKey: 'root-only' })
    await client.instance.request({
      url,
      method: url.endsWith('/cancel') ? 'POST' : 'GET',
    })
    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('')
  },
)

describe('workbench credential isolation', () => {
  it.each([
    '/health',
    '/api/v1/console/dashboard/summary',
    '/api/v1/observer/system',
    '/api/v1/tasks',
    '/api/v1/fs/ls',
  ])(
    'uses only User credentials and never falls back to Root for %s',
    async (path) => {
      const { client, requests } = createRecordingClient()
      client.setOptions({ credentialScope: 'data' })
      client.setConnection({ apiKey: 'user-key', adminApiKey: 'root-key' })
      await client.instance.get(path, {
        headers: { 'X-API-Key': 'root-key', Authorization: 'Bearer root-key' },
      })
      expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('user-key')
      expect(readRequestHeader(requests[0], 'Authorization')).toBe('')
      client.setConnection({ apiKey: '' })
      await client.instance.get(path)
      expect(readRequestHeader(requests[1], 'X-API-Key')).toBe('')
    },
  )
  it('blocks admin requests before transport, including a request queued before switching to the workbench', async () => {
    const { client, requests } = createRecordingClient()
    client.setConnection({ apiKey: 'user-key', adminApiKey: 'root-key' })
    const pending = client.instance.get('/api/v1/admin/accounts')
    client.setOptions({ credentialScope: 'data' })
    await expect(pending).rejects.toMatchObject({ statusCode: 403 })
    expect(requests).toHaveLength(0)
    client.setOptions({ credentialScope: 'control' })
    await client.instance.get('/api/v1/admin/accounts')
    expect(readRequestHeader(requests[0], 'X-API-Key')).toBe('root-key')
  })
})
