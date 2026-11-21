import { beforeEach, expect, it, vi } from 'vitest'
import { createConnectionHealthHeaders } from './auth'
import { fetchServerHealth, resetServerHealthCache } from './health'
import { connectionProfileFingerprint } from './profiles'
import { normalizeConnectionDraft } from './connection-runtime'
import { rememberSurface, restoreSurface } from './surface-context'

const { getHealth } = vi.hoisted(() => ({ getHealth: vi.fn() }))
vi.mock('#/shared/api/ov-client/index', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  getHealth,
}))
const connection = {
  baseUrl: 'http://ov.test',
  accountId: 'acme',
  userId: 'alice',
  apiKey: '',
  adminApiKey: 'synthetic-admin',
  adminDelegation: true,
}
const valid = {
  auth_mode: 'api_key',
  account_id: 'acme',
  user_id: 'alice',
  role: 'user',
  delegated_by_user_id: 'admin',
  capabilities: { admin_user_delegation: true },
}
beforeEach(() => {
  vi.clearAllMocks()
  resetServerHealthCache()
})

it('keeps control authentication independent and never asserts a target with the old user header', () => {
  expect(createConnectionHealthHeaders(connection, 'data')).toEqual({
    Authorization: 'OpenViking-Delegate synthetic-admin',
    'X-OpenViking-Delegate-User': 'alice',
    'X-OpenViking-Account': 'acme',
  })
  expect(
    createConnectionHealthHeaders(connection, 'control'),
  ).not.toHaveProperty('X-OpenViking-Delegate-User')
})

it.each([
  { ...valid, capabilities: undefined },
  { ...valid, role: 'admin' },
  { ...valid, user_id: 'admin' },
  { ...valid, account_id: 'other' },
  { ...valid, delegated_by_user_id: undefined },
])(
  'rejects unsupported or mismatched delegation without accepting an Admin fallback: %j',
  async (health) => {
    getHealth.mockResolvedValue({ data: health })
    await expect(
      fetchServerHealth(
        connection.baseUrl,
        createConnectionHealthHeaders(connection, 'data'),
      ),
    ).rejects.toThrow()
  },
)

it('accepts only a capability-confirmed USER identity from the selected account', async () => {
  getHealth.mockResolvedValue({ data: valid })
  await expect(
    fetchServerHealth(
      connection.baseUrl,
      createConnectionHealthHeaders(connection, 'data'),
    ),
  ).resolves.toEqual(valid)
})

it('persists explicit delegation separately from user credentials and distinguishes profile identity', () => {
  const normalized = normalizeConnectionDraft(connection)
  expect(normalized).toMatchObject({ apiKey: '', adminDelegation: true })
  const saved = rememberSurface(
    { id: 'p', name: 'User view', connection: normalized },
    normalized,
    'workbench',
  )
  expect(restoreSurface(saved, 'workbench').adminDelegation).toBe(true)
  expect(connectionProfileFingerprint(normalized)).not.toBe(
    connectionProfileFingerprint({ ...normalized, adminDelegation: false }),
  )
  expect(
    normalizeConnectionDraft({ ...connection, apiKey: 'personal-user-key' })
      .adminDelegation,
  ).toBeUndefined()
})
