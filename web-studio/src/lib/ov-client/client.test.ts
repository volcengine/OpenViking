import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { describe, expect, it } from 'vitest'

import { createOvClient } from './client'

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
