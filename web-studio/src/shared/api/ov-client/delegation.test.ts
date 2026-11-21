import axios, { AxiosHeaders } from 'axios'
import { expect, it } from 'vitest'
import { createOvClient } from './client'
import { adminDelegationHeaders } from './delegation'

function recording() {
  const requests: Record<string, unknown>[] = []
  const instance = axios.create({
    adapter: async (config) => {
      requests.push(AxiosHeaders.from(config.headers).toJSON())
      return { config, data: {}, headers: {}, status: 200, statusText: 'OK' }
    },
  })
  const client = createOvClient({
    axios: instance,
    baseUrl: 'http://ov.test',
    bindSdkClient: false,
    credentialScope: 'data',
    connection: {
      accountId: 'acme',
      userId: 'alice',
      adminApiKey: 'synthetic-admin',
      adminDelegation: true,
      delegationVerified: true,
    },
  })
  return { client, requests }
}

it('sends one explicit USER delegation, rejects Admin routes, and clears it for control requests', async () => {
  const { client, requests } = recording()
  await client.instance.get('/api/v1/fs/ls', {
    headers: { 'X-OpenViking-User': 'forged' },
  })
  expect(requests[0]).toMatchObject({
    Authorization: 'OpenViking-Delegate synthetic-admin',
    'X-OpenViking-Delegate-User': 'alice',
    'X-OpenViking-Account': 'acme',
  })
  expect(requests[0]).not.toHaveProperty('X-OpenViking-User')
  await expect(
    client.instance.get('/api/v1/admin/accounts'),
  ).rejects.toMatchObject({ statusCode: 403 })
  client.setOptions({ credentialScope: 'control' })
  await client.instance.get('/api/v1/admin/accounts')
  expect(requests[1]).not.toHaveProperty('X-OpenViking-Delegate-User')
  expect(requests).toHaveLength(2)
})

it('blocks REST and raw-stream header creation until current server verification succeeds', async () => {
  const { client, requests } = recording()
  client.setConnection({ delegationVerified: false })
  await expect(client.instance.get('/api/v1/fs/ls')).rejects.toMatchObject({
    statusCode: 403,
  })
  expect(() => adminDelegationHeaders(client.getConnection())).toThrow()
  expect(requests).toHaveLength(0)
})

it('clears delegated state when switching to a personal key and when resetting the adapter', async () => {
  const { client, requests } = recording()
  client.setConnection({ adminDelegation: false, apiKey: 'personal-user-key' })
  await client.instance.get('/api/v1/fs/ls')
  expect(requests[0]['X-API-Key']).toBe('personal-user-key')
  expect(requests[0]).not.toHaveProperty('X-OpenViking-Delegate-User')
  client.clearConnection()
  expect(client.getConnection().adminDelegation).toBeUndefined()
})
