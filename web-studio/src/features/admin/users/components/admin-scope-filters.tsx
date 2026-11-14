import { UserSpaceSwitcher } from './user-space-switcher'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useAdminSpaceHeaders } from '#/features/connection/hooks/use-admin-space-headers'
import {
  fetchServerHealth,
  readServerIdentity,
} from '#/features/connection/lib/health'
import { savedDataIdentities } from '#/features/connection/lib/profiles'
import { createApiKeyHeaders, resolveApiKeyAuth } from '#/shared/api/auth'
import { fetchAdminUsers } from '#/shared/api/admin'
import { FilterSelect } from '#/shared/components/filter-select'
import { useQuery } from '@tanstack/react-query'
import { UserRoundIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

/** Explicit per-request user credentials; selecting a User never changes the login. */
export function useAdminUserScope(
  management: boolean,
  kind: 'usage' | 'tasks',
) {
  const app = useAppConnection()
  const { connection, connectionRole, serverMode, profiles = [] } = app
  const space = useAdminSpaceHeaders(management)
  const [open, setOpen] = useState(false)
  const [selection, setSelection] = useState({ scope: '', user: '' })
  const scope = space.scopeKey
  const userId = selection.scope === scope ? selection.user : ''
  const allowed =
    management &&
    !app.isConnectionRoleLoading &&
    (connectionRole === 'root' || connectionRole === 'admin') &&
    Boolean(connection.adminApiKey.trim())
  const users = useQuery({
    queryKey: ['admin-page-users', scope],
    queryFn: () =>
      fetchAdminUsers(
        { ...connection, apiKey: connection.adminApiKey },
        connection.accountId,
      ),
    enabled: allowed && (open || Boolean(userId)),
    retry: false,
  })
  const saved = allowed ? savedDataIdentities(profiles, connection) : []
  const options = (users.data ?? [])
    .filter(
      (user) =>
        user.role === 'user' || (kind === 'tasks' && user.role === 'admin'),
    )
    .map((user) => ({
      ...user,
      apiKey:
        user.apiKey ||
        saved.find(
          (identity) =>
            identity.accountId === connection.accountId &&
            identity.userId === user.userId,
        )?.apiKey,
    }))
  const selected = options.find((user) => user.userId === userId)
  const candidateHeaders = selected
    ? serverMode === 'trusted'
      ? {
          'X-API-Key': connection.adminApiKey,
          'X-OpenViking-Account': connection.accountId,
          'X-OpenViking-User': selected.userId,
          'X-OpenViking-Role': 'user',
        }
      : createApiKeyHeaders(
          selected.apiKey ?? '',
          resolveApiKeyAuth(connection.baseUrl, connection.serviceSelection),
        )
    : null
  const verified = useQuery({
    queryKey: ['admin-page-user-credential', scope, userId, candidateHeaders],
    queryFn: async ({ signal }) => {
      const health = await fetchServerHealth(
        connection.baseUrl,
        candidateHeaders!,
        signal,
      )
      const identity = readServerIdentity(health)
      if (
        identity.accountId !== connection.accountId ||
        identity.userId !== userId ||
        (kind === 'usage'
          ? health.role !== 'user'
          : health.role !== 'user' && health.role !== 'admin')
      )
        return null
      return candidateHeaders
    },
    enabled:
      allowed &&
      Boolean(selected) &&
      (serverMode === 'trusted' || Boolean(selected?.apiKey)),
    retry: false,
    staleTime: 30_000,
  })
  return {
    ...space,
    headers:
      management && userId
        ? allowed
          ? (verified.data ?? null)
          : null
        : space.headers,
    scopeKey: userId ? `${scope}/user/${userId}` : scope,
    isLoading: userId
      ? users.isPending ||
        (Boolean(selected) &&
          (serverMode === 'trusted' || Boolean(selected?.apiKey)) &&
          verified.isPending &&
          !users.isError)
      : space.isLoading,
    unavailableKey: userId
      ? ('appShell:adminNavigation.userScopeUnavailable' as const)
      : space.unavailableKey,
    userFilter: {
      allowed,
      open,
      setOpen,
      userId,
      kind,
      options,
      loading: users.isPending,
      error: users.isError,
      retry: () => void users.refetch(),
      canAssert: serverMode === 'trusted',
      choose: (user: string) => {
        if (user && user === userId) void verified.refetch()
        setSelection({ scope, user })
        setOpen(false)
      },
    },
  }
}

export function AdminScopeFilters({
  userFilter,
}: {
  userFilter?: ReturnType<typeof useAdminUserScope>['userFilter']
}) {
  const { t } = useTranslation('appShell')
  const { connectionRole, connection, isConnectionRoleLoading } =
    useAppConnection()
  const canManageAccounts =
    !isConnectionRoleLoading &&
    connectionRole === 'root' &&
    Boolean(connection.adminApiKey)
  return (
    <div className="flex flex-wrap items-center gap-3">
      <UserSpaceSwitcher canManageAccounts={canManageAccounts} />
      {userFilter?.allowed && (
        <FilterSelect
          label={t('adminNavigation.userFilter')}
          value={userFilter.userId}
          onValueChange={userFilter.choose}
          icon={<UserRoundIcon className="size-4 shrink-0" />}
          className="users-space-trigger"
          open={userFilter.open}
          onOpenChange={userFilter.setOpen}
          options={[
            {
              value: '',
              label: t(
                userFilter.kind === 'tasks'
                  ? 'adminNavigation.currentTaskUser'
                  : 'adminNavigation.allUsers',
              ),
            },
            ...userFilter.options.map((user) => ({
              value: user.userId,
              label: user.userId,
              disabled: !userFilter.canAssert && !user.apiKey,
              description:
                !userFilter.canAssert && !user.apiKey
                  ? t('adminNavigation.userKeyUnavailable')
                  : undefined,
            })),
          ]}
          loading={userFilter.loading}
          loadingLabel={t('adminNavigation.loadingUsers')}
          error={userFilter.error}
          retryLabel={t('spaces.retry')}
          onRetry={userFilter.retry}
        />
      )}
    </div>
  )
}
