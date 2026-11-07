import type { Connection } from '#/features/admin/vikingbot/api'
import {
  createConnection,
  deleteConnection,
  getBotUsers,
  getMessages,
  rotateCredentials,
  updateConnection,
  verifyConnection,
} from '#/features/admin/vikingbot/api'
import {
  currentOnboarding,
  updateOnboarding,
} from '#/features/admin/vikingbot/providers/feishu/api'
import { beforeEach, expect, it, vi } from 'vitest'

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  accountId: 'team',
}))
vi.mock('#/shared/api/ov-client/index', () => ({
  ovClient: {
    client: transport,
    getConnection: () => ({ accountId: transport.accountId }),
  },
  getOvResult: (result: unknown) => result,
}))
const connection = { id: 'connection', revision: 3 } as Connection
const base = '/api/v1/admin/accounts/team/bot'
beforeEach(() => {
  vi.clearAllMocks()
  transport.accountId = 'team'
})
it('uses explicit lifecycle methods and preserves revision checks', () => {
  updateConnection(connection, 'pause')
  expect(transport.patch).toHaveBeenCalledWith({
    url: `${base}/connections/connection`,
    body: { enabled: false, revision: 3 },
  })
  deleteConnection(connection)
  expect(transport.delete).toHaveBeenCalledWith({
    url: `${base}/connections/connection`,
    query: { revision: 3 },
  })
  verifyConnection(connection)
  expect(transport.post).toHaveBeenCalledWith({
    url: `${base}/connections/connection/verifications`,
    body: { revision: 3 },
  })
})
it('keeps provider credential fields inside a generic envelope', () => {
  const credentials = { client_id: 'id', client_secret: 'secret' }
  createConnection({ type: 'future-platform', user_id: 'bot', credentials })
  expect(transport.post).toHaveBeenCalledWith({
    url: `${base}/connections`,
    body: { type: 'future-platform', user_id: 'bot', credentials },
  })
  rotateCredentials(connection, credentials, 'bot')
  expect(transport.post).toHaveBeenLastCalledWith({
    url: `${base}/connections/connection/credentials`,
    body: { revision: 3, credentials, user_id: 'bot' },
  })
})
it.each(['api_key', 'trusted'] as const)(
  'selects users using the %s auth contract',
  async (mode) => {
    transport.get.mockResolvedValue([
      { user_id: 'bot', api_key_available: true },
      { user_id: 'keyless', api_key_available: false },
    ])
    expect(await getBotUsers(mode)).toEqual([
      { user_id: 'bot', available: true },
      { user_id: 'keyless', available: mode === 'trusted' },
    ])
    expect(transport.get).toHaveBeenCalledWith({
      url: '/api/v1/admin/accounts/team/users',
      query: { role: 'user', include_credentials: false },
    })
  },
)
it('uses current account and encodes identifiers without moving conversation keys into paths', () => {
  transport.accountId = 'team two'
  getMessages('app/id', 'group/thread:topic', 7)
  expect(transport.get).toHaveBeenCalledWith({
    url: '/api/v1/admin/accounts/team%20two/bot/connections/app%2Fid/messages',
    query: { conversation: 'group/thread:topic', before: 7 },
  })
})
it('uses provider selection for current setup and explicit job actions', () => {
  currentOnboarding()
  expect(transport.get).toHaveBeenCalledWith({
    url: `${base}/onboarding-runs/current`,
    query: { type: 'feishu' },
  })
  updateOnboarding('job', 'retry')
  expect(transport.post).toHaveBeenCalledWith({
    url: `${base}/onboarding-runs/job/actions`,
    body: { action: 'retry' },
  })
})
