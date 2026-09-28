import axios from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import {
  createAdminGroup,
  deleteAdminGroup,
  fetchAdminGroupMembers,
  fetchAdminGroups,
  updateAdminGroupMember,
} from './admin'
import type { InternalAxiosRequestConfig } from 'axios'

const connection = {
  baseUrl: 'http://localhost:1933/',
  accountId: 'tenant a',
  userId: 'data-user',
  apiKey: 'control-key',
}
afterEach(() => vi.restoreAllMocks())

it('uses the control credential and encodes account, group and user path segments', async () => {
  const requests: InternalAxiosRequestConfig[] = []
  const transport = axios.create({
    adapter: async (config) => {
      requests.push(config)
      return {
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
        data: { status: 'ok', result: { added: true } },
      }
    },
  })
  vi.spyOn(axios, 'create').mockReturnValue(transport)
  await updateAdminGroupMember(connection, 'group/a', 'bob@example.com', true)
  expect(requests[0].url).toBe(
    'http://localhost:1933/api/v1/admin/accounts/tenant%20a/groups/group%2Fa/members/bob%40example.com',
  )
  expect(requests[0].method).toBe('put')
  expect(requests[0].headers.get('X-API-Key')).toBe('control-key')
  expect(requests[0].headers.get('X-OpenViking-User')).toBeUndefined()
})

it('matches group CRUD and member endpoint contracts and unwraps results', async () => {
  const requests: InternalAxiosRequestConfig[] = []
  const groups = [{ group_id: 'team', member_count: 0 }]
  const transport = axios.create({
    adapter: async (config) => {
      requests.push(config)
      return {
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
        data: { status: 'ok', result: groups },
      }
    },
  })
  vi.spyOn(axios, 'create').mockReturnValue(transport)
  expect(await fetchAdminGroups(connection)).toEqual(groups)
  await createAdminGroup(connection, 'team')
  expect(JSON.parse(requests[1].data)).toEqual({ group_id: 'team' })
  await fetchAdminGroupMembers(connection, 'team')
  await updateAdminGroupMember(connection, 'team', 'alice', false)
  await deleteAdminGroup(connection, 'team')
  expect(requests.map((request) => request.method)).toEqual([
    'get',
    'post',
    'get',
    'delete',
    'delete',
  ])
  expect(requests[2].url).toMatch(/\/groups\/team\/members$/)
  expect(requests[3].url).toMatch(/\/groups\/team\/members\/alice$/)
  expect(requests[4].url).toMatch(/\/groups\/team$/)
})
