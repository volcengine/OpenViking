import { fetchAdminGroups, fetchAdminUsersPage } from '#/shared/api/admin'
import type { AdminConnection } from '#/shared/api/admin'

export const DEFAULT_ADMIN_USER_PAGE = { page: 1, pageSize: 20, search: '' }

export function adminUsersQuery(
  connection: AdminConnection,
  options = DEFAULT_ADMIN_USER_PAGE,
) {
  const { accountId, apiKey, baseUrl } = connection
  const { page, pageSize, search } = options
  return {
    queryKey: [
      'managed-users',
      baseUrl,
      apiKey,
      accountId,
      page,
      pageSize,
      search,
    ],
    queryFn: () => fetchAdminUsersPage(connection, accountId, options),
    retry: false as const,
  }
}

export function adminGroupsQuery(connection: AdminConnection) {
  const { accountId, apiKey, baseUrl } = connection
  return {
    queryKey: ['managed-groups', baseUrl, accountId, apiKey],
    queryFn: () => fetchAdminGroups(connection),
    retry: false as const,
  }
}
