import { useQueries, useQuery } from '@tanstack/react-query'
import { fetchAdminGroupMembers, fetchAdminGroups } from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'

export function useUserGroupMemberships(
  connection: AdminConnection,
  enabled: boolean,
) {
  const scope = [connection.baseUrl, connection.accountId, connection.apiKey]
  const groups = useQuery({
    queryKey: ['managed-groups', ...scope],
    queryFn: () => fetchAdminGroups(connection),
    enabled,
    retry: false,
  })
  const populatedGroups =
    groups.data?.filter((group) => group.member_count > 0) ?? []
  const members = useQueries({
    queries: populatedGroups.map((group) => ({
      queryKey: ['managed-group-members', ...scope, group.group_id],
      queryFn: () => fetchAdminGroupMembers(connection, group.group_id),
      enabled,
      retry: false,
    })),
  })
  const byUser = new Map<string, string[]>()
  members.forEach((query, index) => {
    query.data?.members.forEach((userId) => {
      const groupIds = byUser.get(userId) ?? []
      groupIds.push(populatedGroups[index].group_id)
      byUser.set(userId, groupIds)
    })
  })
  const status =
    groups.isError || members.some((query) => query.isError)
      ? 'error'
      : groups.isPending || members.some((query) => query.isPending)
        ? 'loading'
        : 'ready'

  return { byUser, status }
}
