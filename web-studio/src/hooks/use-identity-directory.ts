import * as React from 'react'
import { useQuery } from '@tanstack/react-query'

import { useAppConnection } from '#/hooks/use-app-connection'
import { fetchAdminAccounts, fetchAdminUsers } from '#/lib/admin'
import { DEFAULT_ACCOUNT_ID, DEFAULT_USER_ID } from '#/lib/admin-options'
import type { AdminAccount, AdminConnection, AdminUser } from '#/lib/admin'
import type { IdentityDirectory } from '#/components/identity-picker'

export type UseIdentityDirectory = IdentityDirectory & {
  resolveUserKey: (accountId: string, userId: string) => string | undefined
}

/**
 * Builds the account/user directory backing the OAuth consent "select a
 * specific identity" mode, mirroring the Studio Connection & Identity page.
 *
 * Availability is gated on `api_key` server mode (trusted/dev modes never
 * expose per-user keys, and the backend rejects those identities for OAuth)
 * plus a root/admin caller. ROOT lists every account; an account-admin gets a
 * 403 on `GET /admin/accounts` and is therefore pinned to their own account.
 */
export function useIdentityDirectory(): UseIdentityDirectory {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()

  const controlApiKey =
    connection.adminApiKey.trim() || connection.apiKey.trim()
  const baseAccountId = connection.accountId || DEFAULT_ACCOUNT_ID
  const baseUserId = connection.userId || DEFAULT_USER_ID

  const available =
    serverMode === 'api_key' &&
    !isConnectionRoleLoading &&
    (connectionRole === 'root' || connectionRole === 'admin') &&
    Boolean(controlApiKey)

  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: baseAccountId,
      apiKey: controlApiKey,
      baseUrl: connection.baseUrl,
      userId: baseUserId,
    }),
    [baseAccountId, baseUserId, controlApiKey, connection.baseUrl],
  )

  // ROOT can enumerate every account; account-admins get 403 here. Query keys
  // mirror the settings page so navigating between the two reuses cache.
  const accountsQuery = useQuery({
    enabled: available,
    queryFn: () => fetchAdminAccounts(adminConnection),
    queryKey: [
      'admin-accounts',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      adminConnection.accountId,
      adminConnection.userId,
    ],
    retry: false,
  })

  const isRoot = accountsQuery.isSuccess
  const accounts: AdminAccount[] = accountsQuery.data ?? [
    { accountId: baseAccountId, userCount: 0 },
  ]

  const [selectedAccountId, setSelectedAccountId] =
    React.useState(baseAccountId)
  const [selectedUserId, setSelectedUserId] = React.useState(baseUserId)

  // Account-admins cannot switch accounts: keep selection pinned to their own.
  const effectiveAccountId = isRoot ? selectedAccountId : baseAccountId

  const usersQuery = useQuery({
    enabled: available && Boolean(effectiveAccountId),
    queryFn: () => fetchAdminUsers(adminConnection, effectiveAccountId),
    queryKey: [
      'admin-users',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      adminConnection.accountId,
      adminConnection.userId,
      effectiveAccountId,
    ],
    retry: false,
  })

  const users: AdminUser[] = usersQuery.data ?? []

  // Once the user list for the selected account arrives, ensure the selected
  // user is valid; otherwise fall back to a sensible default.
  React.useEffect(() => {
    if (!users.length) {
      return
    }
    const ids = users.map((user) => user.userId)
    if (ids.includes(selectedUserId)) {
      return
    }
    const preferred = ids.includes(baseUserId)
      ? baseUserId
      : ids.includes(DEFAULT_USER_ID)
        ? DEFAULT_USER_ID
        : ids[0]
    setSelectedUserId(preferred)
  }, [users, selectedUserId, baseUserId])

  const onSelectAccount = React.useCallback((accountId: string) => {
    setSelectedAccountId(accountId)
    // Reset to default; the reconciliation effect picks a valid user once the
    // new account's user list loads.
    setSelectedUserId(DEFAULT_USER_ID)
  }, [])

  const onSelectUser = React.useCallback((userId: string) => {
    setSelectedUserId(userId)
  }, [])

  const resolveUserKey = React.useCallback(
    (accountId: string, userId: string) =>
      users.find(
        (user) => user.accountId === accountId && user.userId === userId,
      )?.apiKey,
    [users],
  )

  return {
    available,
    isRoot,
    accounts,
    users,
    selectedAccountId: effectiveAccountId,
    selectedUserId,
    onSelectAccount,
    onSelectUser,
    accountsLoading: accountsQuery.isLoading,
    usersLoading: usersQuery.isLoading,
    resolveUserKey,
  }
}
