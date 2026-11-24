import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fetchConnectionHealth } from './health'
import { verifyManagementKey } from './management-key'
import { OvClientError } from '#/shared/api/ov-client/errors'

vi.mock('./health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchConnectionHealth: vi.fn(),
}))
const connection = {
  baseUrl: 'http://ov.test',
  accountId: 'a',
  userId: 'alice',
  apiKey: 'alice-key',
  adminApiKey: '',
  serviceSelection: 'opensource' as const,
}
beforeEach(() => vi.clearAllMocks())
afterEach(() => vi.restoreAllMocks())

it.each(['root', 'admin'])(
  'accepts %s using only the submitted key without asserted user or account headers',
  async (role) => {
    vi.mocked(fetchConnectionHealth).mockResolvedValue({
      version: 'test',
      auth_mode: 'api_key',
      role,
      account_id: 'b',
    })
    const signal = new AbortController().signal
    await expect(
      verifyManagementKey(connection, '  control-key  ', signal),
    ).resolves.toBe('control-key')
    expect(fetchConnectionHealth).toHaveBeenCalledWith(
      {
        ...connection,
        apiKey: '',
        adminApiKey: 'control-key',
        accountId: '',
        userId: '',
      },
      signal,
      'control',
    )
    expect(connection).toMatchObject({
      apiKey: 'alice-key',
      accountId: 'a',
      userId: 'alice',
      adminApiKey: '',
    })
  },
)

it.each([
  [{ version: 'test', role: 'user', account_id: 'a' }, 'notAdmin'],
  [{ version: 'test', role: 'admin' }, 'identityMissing'],
  [{ version: 'test', role: 'unknown' }, 'notAdmin'],
  [{}, 'invalidService'],
])(
  'rejects insufficient or malformed management identity %j',
  async (health, code) => {
    vi.mocked(fetchConnectionHealth).mockResolvedValue(health)
    await expect(
      verifyManagementKey(
        connection,
        'wrong-key',
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ code })
  },
)

it('preserves the HTTP status while redacting a failed submitted key', async () => {
  vi.mocked(fetchConnectionHealth).mockRejectedValue(
    new OvClientError({
      statusCode: 401,
      code: 'UNAUTHENTICATED',
      message: 'Invalid secret-key',
    }),
  )
  await expect(
    verifyManagementKey(connection, 'secret-key', new AbortController().signal),
  ).rejects.toMatchObject({
    statusCode: 401,
    message: 'HTTP 401: Invalid [redacted]',
  })
})

it('ignores successful responses after cancellation', async () => {
  const controller = new AbortController()
  vi.mocked(fetchConnectionHealth).mockImplementation(async () => {
    controller.abort()
    return { version: 'test', role: 'root' }
  })
  await expect(
    verifyManagementKey(connection, 'root-key', controller.signal),
  ).rejects.toThrow()
})
