// @vitest-environment jsdom
import { AxiosError } from 'axios'
import type { AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  GatewayError,
  getAccess,
  deleteUserData,
  getConnectionInfo,
  issueKey,
  listKeyUsers,
  listLogs,
  listTools,
  newObjectId,
  resyncCapture,
  saveUpstream,
  toGatewayError,
} from './api'
import { getManagementKey, setManagementKey } from './management-key'
import { UPSTREAM_DEFAULTS } from './upstream-schema'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('#/lib/admin', () => ({
  createAdminClient: () => ({ request }),
}))

const connection = {
  accountId: 'acme',
  apiKey: 'admin-secret',
  baseUrl: 'https://ov.example.com',
  userId: 'admin',
}

function ok(data: unknown) {
  return { data, headers: {}, status: 200 }
}

function failure(status: number, data: unknown) {
  return new AxiosError(
    `Request failed with status code ${status}`,
    'ERR_BAD_RESPONSE',
    undefined,
    undefined,
    { status, data, headers: {}, statusText: '' } as AxiosResponse,
  )
}

beforeEach(() => {
  request.mockReset()
  sessionStorage.clear()
})

describe('gateway calls', () => {
  it('sends JSON bodies to encoded paths and keeps secrets out of the URL', async () => {
    request.mockResolvedValue(ok({ id: 'a/b', revision: 1 }))
    const input = { ...UPSTREAM_DEFAULTS, name: 'Chat', api_key: 'sk-secret' }
    await saveUpstream(connection, 'a/b', input)
    const [options] = request.mock.calls[0]
    expect(options).toMatchObject({
      method: 'PUT',
      url: '/gateway/admin/upstreams/a%2Fb',
      body: input,
      headers: { 'Content-Type': 'application/json' },
    })
    expect(options.url).not.toContain('sk-secret')
    expect(options.url).not.toContain('admin-secret')
  })

  it('passes the log limit as a query parameter and returns plain bodies', async () => {
    request.mockResolvedValue(ok([{ time: 1, kind: 'user' }]))
    await expect(listLogs(connection, 50)).resolves.toEqual([
      { time: 1, kind: 'user' },
    ])
    expect(request.mock.calls[0][0]).toMatchObject({
      method: 'GET',
      url: '/gateway/admin/logs',
      query: { limit: 50 },
    })
    expect(request.mock.calls[0][0]).not.toHaveProperty('body')
  })

  it('loads raw MCP names and optional annotations from the tools endpoint', async () => {
    const tools = [
      {
        name: 'read',
        description: 'Read a file',
        annotations: { readOnlyHint: true },
      },
      { name: 'future_tool', description: 'A new MCP tool' },
    ]
    request.mockResolvedValue(ok(tools))
    await expect(listTools(connection)).resolves.toEqual(tools)
    expect(request.mock.calls[0][0]).toMatchObject({
      method: 'GET',
      url: '/gateway/admin/tools',
    })
    request.mockResolvedValue(ok([]))
    await expect(listTools(connection)).resolves.toEqual([])
    request.mockRejectedValue(failure(503, { detail: 'Tools unavailable' }))
    await expect(listTools(connection)).rejects.toMatchObject({
      status: 503,
      detail: 'Tools unavailable',
    })
  })

  it('addresses resync, key issuance and user data deletion', async () => {
    request.mockResolvedValue(ok({}))
    await resyncCapture(connection, 'key-1', { session: 's', protocol: 'chat' })
    await issueKey(connection, {
      name: 'Laptop',
      user_id: 'alice',
      policy_id: 'p',
      upstream_ids: ['u'],
      models: [],
    })
    await deleteUserData(connection, 'alice')
    expect(
      request.mock.calls.map(([options]) => [options.method, options.url]),
    ).toEqual([
      ['POST', '/gateway/admin/keys/key-1/capture/reset'],
      ['POST', '/gateway/admin/keys'],
      ['DELETE', '/gateway/admin/users/alice/data'],
    ])
  })
})

describe('listKeyUsers', () => {
  it("lists the account's users and admins without their credentials", async () => {
    request.mockResolvedValue(
      ok({
        status: 'ok',
        result: [
          { user_id: 'alice', role: 'user', api_key_available: true },
          { user_id: 'boss', role: 'admin', api_key_available: false },
          { user_id: 'root', role: 'root', api_key_available: true },
        ],
      }),
    )
    await expect(
      listKeyUsers({ ...connection, accountId: 'acme/team' }),
    ).resolves.toEqual([
      { user_id: 'alice', role: 'user', api_key_available: true },
      { user_id: 'boss', role: 'admin', api_key_available: false },
    ])
    expect(request.mock.calls[0][0]).toMatchObject({
      method: 'GET',
      url: '/api/v1/admin/accounts/acme%2Fteam/users',
      query: { include_credentials: false },
    })
  })
})

describe('toGatewayError', () => {
  it.each([
    [
      'explicit in-use reason',
      409,
      { reason: 'in_use', key_count: 3, detail: 'dependent keys' },
      'in_use',
      'dependent keys',
    ],
    [
      'disabled gateway',
      404,
      { reason: 'not_enabled', detail: 'disabled' },
      'not_enabled',
      'disabled',
    ],
    [
      'invalid management key',
      401,
      { reason: 'management_key_invalid', detail: 'invalid management key' },
      'management_key_invalid',
      'invalid management key',
    ],
    [
      'validation list',
      422,
      { detail: [{ msg: 'Field required' }, { msg: 'Input too long' }] },
      'invalid',
      'Field required; Input too long',
    ],
    [
      'unknown wording without a reason',
      503,
      { detail: 'OpenViking Gateway management token is not configured' },
      'other',
      'OpenViking Gateway management token is not configured',
    ],
  ])('reads %s', async (_, status, body, reason, detail) => {
    request.mockRejectedValue(failure(status, body))
    const thrown = await listLogs(connection).catch((caught) => caught)
    expect(thrown).toBeInstanceOf(GatewayError)
    expect([thrown.status, thrown.reason, thrown.detail]).toEqual([
      status,
      reason,
      detail,
    ])
    expect(thrown.unavailable).toBe(reason === 'not_enabled')
  })

  it('reads a missing gateway route as a server without gateway support', async () => {
    request.mockRejectedValue(
      failure(404, { status: 'error', error: { message: 'Not Found' } }),
    )
    const thrown = await getConnectionInfo(connection).catch((caught) => caught)
    expect([thrown.status, thrown.reason, thrown.unavailable]).toEqual([
      404,
      'not_enabled',
      true,
    ])
    // Elsewhere a 404 still means the item is gone.
    const elsewhere = await listLogs(connection).catch((caught) => caught)
    expect(elsewhere.reason).toBe('not_found')
  })

  it('falls back to the message for network failures and plain errors', () => {
    const network = toGatewayError(
      new AxiosError('Network Error', AxiosError.ERR_NETWORK),
    )
    expect([network.status, network.reason, network.detail]).toEqual([
      undefined,
      'other',
      'Network Error',
    ])
    expect(toGatewayError(new Error('boom')).detail).toBe('boom')
    const error = new GatewayError('x', 404)
    expect(toGatewayError(error)).toBe(error)
  })
})

it('creates distinct hex ids without crypto.randomUUID', () => {
  const ids = new Set(Array.from({ length: 50 }, newObjectId))
  expect(ids.size).toBe(50)
  for (const id of ids) expect(id).toMatch(/^[0-9a-f]{16}$/)
})

it('sends management secrets only to gateway calls, with account scope', async () => {
  setManagementKey(connection.baseUrl, 'ovgm-management-secret')
  request.mockResolvedValue(ok([]))
  await listLogs(connection)
  expect(request.mock.calls[0][0]).toMatchObject({
    query: { account_id: 'acme' },
    headers: {
      'X-OpenViking-Gateway-Management-Key': 'ovgm-management-secret',
    },
  })
  await listKeyUsers(connection)
  expect(request.mock.calls[1][0].headers).toBeUndefined()
})
it('locks an expired stored key but does not store an unverified candidate', async () => {
  setManagementKey(connection.baseUrl, 'old-key')
  request.mockRejectedValue(
    failure(401, { reason: 'management_key_invalid', detail: 'rotated' }),
  )
  await expect(
    getAccess({ ...connection, managementKey: 'wrong-candidate' }),
  ).rejects.toMatchObject({
    reason: 'management_key_invalid',
  })
  expect(getManagementKey(connection.baseUrl)).toBe('old-key')
  await expect(getAccess(connection)).rejects.toMatchObject({
    reason: 'management_key_invalid',
  })
  expect(getManagementKey(connection.baseUrl)).toBe('')
})

it('uses the query credential snapshot even after the tab is unlocked or locked', async () => {
  request.mockResolvedValue(ok([]))
  setManagementKey(connection.baseUrl, 'new-management-key')
  await listLogs({ ...connection, managementKey: '' })
  expect(request.mock.calls[0][0].headers).toBeUndefined()
  sessionStorage.clear()
  await listLogs({ ...connection, managementKey: 'captured-key' })
  expect(request.mock.calls[1][0].headers).toEqual({
    'X-OpenViking-Gateway-Management-Key': 'captured-key',
  })
})
