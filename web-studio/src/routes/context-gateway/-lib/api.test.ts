import { AxiosError } from 'axios'
import type { AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  GatewayError,
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
})

describe('gateway calls', () => {
  it('sends JSON bodies to encoded paths and keeps secrets out of the URL', async () => {
    request.mockResolvedValue(ok({ id: 'a/b', revision: 1 }))
    const input = { ...UPSTREAM_DEFAULTS, name: 'Chat', api_key: 'sk-secret' }
    await saveUpstream(connection, 'a/b', input)
    const [options] = request.mock.calls[0]
    expect(options).toMatchObject({
      method: 'PUT',
      url: '/api/v1/admin/context-gateway/upstreams/a%2Fb',
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
      url: '/api/v1/admin/context-gateway/logs',
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
      url: '/api/v1/admin/context-gateway/tools',
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
      ['POST', '/api/v1/admin/context-gateway/keys/key-1/capture/reset'],
      ['POST', '/api/v1/admin/context-gateway/keys'],
      ['DELETE', '/api/v1/admin/context-gateway/users/alice/data'],
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
      'a FastAPI detail',
      409,
      { detail: 'Revoke or update dependent keys before deleting this object' },
      'conflict',
      'Revoke or update dependent keys before deleting this object',
    ],
    [
      'a disabled gateway (OpenViking envelope)',
      503,
      {
        status: 'error',
        error: {
          code: 'UNAVAILABLE',
          message: 'Context Gateway is not enabled',
        },
      },
      'not_enabled',
      'Context Gateway is not enabled',
    ],
    [
      'a missing management token',
      503,
      {
        status: 'error',
        error: {
          code: 'UNAVAILABLE',
          message: 'Context Gateway management token is not configured',
        },
      },
      'token_missing',
      'Context Gateway management token is not configured',
    ],
    [
      'an unreachable gateway',
      503,
      {
        status: 'error',
        error: {
          code: 'UNAVAILABLE',
          message: 'Context Gateway management service is unavailable',
          details: { original_http_status_code: 502 },
        },
      },
      'unreachable',
      'Context Gateway management service is unavailable',
    ],
    [
      'a management token that differs from the gateway',
      401,
      { detail: 'Invalid gateway management credential' },
      'token_mismatch',
      'Invalid gateway management credential',
    ],
    [
      'an OpenViking reason from key issuance',
      403,
      { error: { message: 'root_key_not_allowed' } },
      'forbidden',
      'root_key_not_allowed',
    ],
    [
      'a validation list',
      422,
      { detail: [{ msg: 'Field required' }, { msg: 'Input too long' }] },
      'invalid',
      'Field required; Input too long',
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
    expect(thrown.unavailable).toBe(
      [
        'not_enabled',
        'token_missing',
        'token_mismatch',
        'unreachable',
      ].includes(reason),
    )
  })

  it('reads a missing gateway route as a server without gateway support', async () => {
    request.mockRejectedValue(
      failure(404, { status: 'error', error: { message: 'Not Found' } }),
    )
    const thrown = await getConnectionInfo(connection).catch((caught) => caught)
    expect([thrown.status, thrown.reason, thrown.unavailable]).toEqual([
      404,
      'unsupported',
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
