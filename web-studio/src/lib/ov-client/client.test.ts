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

function createRecordingClient() {
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
    baseUrl: 'http://openviking.test',
    bindSdkClient: false,
  })
  return { client, requests }
}

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
})
