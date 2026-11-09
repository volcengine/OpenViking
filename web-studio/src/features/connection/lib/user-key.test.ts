import { fetchConnectionHealth } from '#/features/connection/lib/health'
import { verifyStudioSetup } from '#/features/connection/lib/onboarding'
import {
  validateUserKeySearch,
  verifyUserKey,
} from '#/features/connection/lib/user-key'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchConnectionHealth: vi.fn(),
}))
vi.mock('#/features/connection/lib/onboarding', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  verifyStudioSetup: vi.fn(),
}))
const source = {
  baseUrl: 'https://service.test',
  serviceSelection: 'opensource' as const,
  accountId: 'team',
  userId: 'alice',
  apiKey: 'alice-key',
  adminApiKey: 'root-key',
}
beforeEach(() => {
  vi.clearAllMocks()
})
it('accepts only the workbench destination and ignores arbitrary redirect targets', () => {
  expect(validateUserKeySearch({ returnTo: '/home' })).toEqual({
    returnTo: '/home',
  })
  for (const returnTo of [
    '/admin',
    'https://other.test',
    '//other.test',
    undefined,
  ]) {
    expect(validateUserKeySearch({ returnTo })).toEqual({})
  }
})
it('resolves the User automatically under the current service/account without submitting Root, and preserves Root', async () => {
  vi.mocked(fetchConnectionHealth).mockResolvedValue({
    auth_mode: 'api_key',
    role: 'user',
    account_id: 'team',
    user_id: 'bob',
  })
  vi.mocked(verifyStudioSetup).mockResolvedValue({
    ...source,
    apiKey: 'bob-key',
    userId: 'bob',
    adminApiKey: '',
  })
  const signal = new AbortController().signal
  const result = await verifyUserKey(source, ' bob-key ', signal)
  expect(fetchConnectionHealth).toHaveBeenCalledWith(
    {
      ...source,
      apiKey: 'bob-key',
      adminApiKey: '',
      accountId: '',
      userId: '',
    },
    signal,
  )
  expect(verifyStudioSetup).toHaveBeenCalledWith(
    { ...source, apiKey: 'bob-key', adminApiKey: '', userId: 'bob' },
    signal,
  )
  expect(result).toEqual({ ...source, apiKey: 'bob-key', userId: 'bob' })
})
it.each([
  [
    { auth_mode: 'api_key', role: 'root', account_id: 'team', user_id: 'root' },
    'rootKey',
  ],
  [
    { auth_mode: 'api_key', role: 'user', account_id: 'other', user_id: 'bob' },
    'accountMismatch',
  ],
  [{ auth_mode: 'api_key', role: 'user' }, 'identityMissing'],
])('rejects invalid scope before any data request', async (health, code) => {
  vi.mocked(fetchConnectionHealth).mockResolvedValue(health)
  await expect(
    verifyUserKey(source, 'key', new AbortController().signal),
  ).rejects.toMatchObject({ code })
  expect(verifyStudioSetup).not.toHaveBeenCalled()
})
it('does not continue verification after cancellation', async () => {
  vi.mocked(fetchConnectionHealth).mockResolvedValue({
    auth_mode: 'api_key',
    role: 'user',
    account_id: 'team',
    user_id: 'bob',
  })
  const controller = new AbortController()
  controller.abort()
  await expect(
    verifyUserKey(source, 'key', controller.signal),
  ).rejects.toThrow()
  expect(verifyStudioSetup).not.toHaveBeenCalled()
})
