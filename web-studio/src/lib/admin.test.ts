import { beforeEach, describe, expect, it, vi } from 'vitest'
import type * as Client from '#/gen/ov-client/client'
import { fetchAdminUsersPage } from './admin'

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }))
vi.mock('#/gen/ov-client/client', async (importOriginal) => {
  const original = await importOriginal<typeof Client>()
  return {
    ...original,
    createClient: () => ({ ...original.createClient(), get: getMock }),
  }
})

const connection = {
  accountId: 'default',
  apiKey: 'test-key',
  baseUrl: 'http://localhost:1933',
  userId: 'root',
}

describe('fetchAdminUsersPage', () => {
  beforeEach(() => {
    getMock.mockReset()
  })

  it('requests only the selected page and uses server totals', async () => {
    getMock.mockResolvedValue({
      status: 200,
      headers: {},
      data: {
        status: 'ok',
        result: {
          users: [{ user_id: 'user-2833', role: 'admin' }],
          total: 1,
          account_total: 2833,
          manager_count: 2,
          key_count: 2830,
        },
      },
    })
    const result = await fetchAdminUsersPage(connection, 'customer_agent_as', {
      page: 2,
      pageSize: 20,
      search: ' USER-2833 ',
    })
    expect(getMock).toHaveBeenCalledWith({
      url: '/api/v1/admin/accounts/{account_id}/users',
      path: { account_id: 'customer_agent_as' },
      query: { page: 2, limit: 20, query: 'USER-2833', include_summary: true },
    })
    expect(result).toMatchObject({
      total: 1,
      accountTotal: 2833,
      managerCount: 2,
      keyCount: 2830,
      users: [
        { accountId: 'customer_agent_as', userId: 'user-2833', role: 'admin' },
      ],
    })
  })

  it('propagates errors instead of falling back to an unbounded request', async () => {
    getMock.mockRejectedValue(new Error('Request failed'))
    await expect(
      fetchAdminUsersPage(connection, 'account', {
        page: 1,
        pageSize: 20,
        search: '',
      }),
    ).rejects.toMatchObject({ message: 'Request failed' })
    expect(getMock).toHaveBeenCalledTimes(1)
  })
})
