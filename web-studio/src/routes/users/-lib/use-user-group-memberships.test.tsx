// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useUserGroupMemberships } from './use-user-group-memberships'

const api = vi.hoisted(() => ({
  groups: vi.fn(),
  members: vi.fn(),
}))
vi.mock('#/lib/admin', () => ({
  fetchAdminGroups: api.groups,
  fetchAdminGroupMembers: api.members,
}))

const connection = {
  baseUrl: 'http://localhost:1933',
  accountId: 'acme',
  userId: 'admin',
  apiKey: 'admin-key',
}

beforeEach(() => {
  vi.resetAllMocks()
  api.groups.mockResolvedValue([
    { group_id: 'fe-dev', member_count: 2 },
    { group_id: 'reviewers', member_count: 1 },
    { group_id: 'empty', member_count: 0 },
  ])
  api.members.mockImplementation(async (_connection, groupId) => ({
    group_id: groupId,
    members: groupId === 'fe-dev' ? ['alice', 'bob'] : ['alice'],
  }))
})
afterEach(cleanup)

it('maps users to their groups and skips empty groups', async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => useUserGroupMemberships(connection, true),
    { wrapper },
  )
  await waitFor(() => expect(result.current.status).toBe('ready'))
  expect(result.current.byUser.get('alice')).toEqual(['fe-dev', 'reviewers'])
  expect(result.current.byUser.get('bob')).toEqual(['fe-dev'])
  expect(api.members).toHaveBeenCalledTimes(2)
})

it('reports incomplete membership data as an error', async () => {
  const error = new Error('Failed to load')
  api.members.mockRejectedValueOnce(error)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => useUserGroupMemberships(connection, true),
    { wrapper },
  )
  await waitFor(() => expect(result.current.status).toBe('error'))
  expect(result.current.errors).toEqual([{ groupId: 'fe-dev', error }])
})

it('preserves the group-list request error for diagnosis', async () => {
  const error = new Error('Forbidden')
  api.groups.mockRejectedValue(error)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => useUserGroupMemberships(connection, true),
    { wrapper },
  )
  await waitFor(() => expect(result.current.status).toBe('error'))
  expect(result.current.errors).toEqual([{ groupId: undefined, error }])
})
