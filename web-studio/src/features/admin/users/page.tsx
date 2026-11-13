import { UserApiKeyCell } from './components/user-api-key-cell'
import { UserSpaceSwitcher } from './components/user-space-switcher'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '#/shared/ui/dropdown-menu'
import { adminUsersQuery } from './lib/admin-membership-queries'
import { ErrorMessage } from '#/shared/components/error-message'
import { showErrorToast } from '#/shared/lib/error-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Link,
  Outlet,
  useNavigate,
  useRouterState,
} from '@tanstack/react-router'
import {
  KeyRoundIcon,
  LoaderCircleIcon,
  PlusIcon,
  RotateCwIcon,
  CircleHelpIcon,
  SearchIcon,
  EllipsisIcon,
  ShieldAlertIcon,
  Trash2Icon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { UserPagination } from '#/features/admin/users/components/user-pagination'
import { useUserGroupMemberships } from '#/features/admin/users/lib/use-user-group-memberships'
import { useUserList } from '#/features/admin/users/lib/use-user-list'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import type {
  AdminConnection,
  AdminUser,
  CreateUserInput,
  KeyResult,
  UpdateUserRoleInput,
} from '#/shared/api/admin'
import {
  createAdminUser,
  regenerateAdminUserKey,
  removeAdminUser,
  updateAdminUserRole,
} from '#/shared/api/admin'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/shared/ui/alert-dialog'
import { Badge } from '#/shared/ui/badge'
import { Button } from '#/shared/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'
import { Input } from '#/shared/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/shared/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '#/shared/ui/tooltip'

import { AddUserDialog } from '#/features/admin/users/components/add-user-dialog'
import { RootSetupGuide } from '#/features/admin/users/components/root-setup-guide'
import { UserMemoryPolicyCell } from '#/features/admin/users/components/user-memory-policy-cell'
import { getErrorMessage } from '#/shared/lib/error'

export function UserManagementRoute() {
  const { t } = useTranslation(['appShell', 'settings'])
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const current = pathname.replace(/\/$/, '')
  if (current !== '/admin/users' && current !== '/admin/users/groups')
    return <Outlet />

  return (
    <section className="studio-users-page">
      <header className="users-page-heading">
        <h1>{t('adminNavigation.users')}</h1>
        <Tooltip>
          <TooltipTrigger
            aria-label={t('userManagement.help')}
            render={<button type="button" />}
          >
            <CircleHelpIcon />
          </TooltipTrigger>
          <TooltipContent>{t('userManagement.help')}</TooltipContent>
        </Tooltip>
      </header>
      <div className="users-security-notice">
        <ShieldAlertIcon aria-hidden="true" />
        <div>
          <p>{t('userManagement.keyNotice')}</p>
          <p>{t('userManagement.agentNotice')}</p>
        </div>
      </div>
      <Outlet />
    </section>
  )
}

export function UserManagementPanel({
  navigation,
}: { navigation?: React.ReactNode } = {}) {
  const { t } = useTranslation('settings')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    setGeneratedCredential,
    serverMode,
    switchIdentity,
    profiles = [],
    activeProfileId,
  } = useAppConnection()
  const workbenchIdentity = profiles.find(
    (profile) => profile.id === activeProfileId,
  )?.workbenchIdentity
  const [addUserOpen, setAddUserOpen] = React.useState(false)
  const [pendingRegenerateUser, setPendingRegenerateUser] =
    React.useState<AdminUser | null>(null)
  const [pendingRemoveUser, setPendingRemoveUser] =
    React.useState<AdminUser | null>(null)
  const [pendingRoleChange, setPendingRoleChange] =
    React.useState<UpdateUserRoleInput | null>(null)
  const [switchingIdentityKey, setSwitchingIdentityKey] = React.useState('')

  const { canManageAccounts, canManageUsers } =
    resolveStudioManagementCapabilities({
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })

  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: connection.accountId,
      apiKey: connection.adminApiKey,
      baseUrl: connection.baseUrl,
      userId: connection.userId,
    }),
    [
      connection.accountId,
      connection.adminApiKey,
      connection.baseUrl,
      connection.userId,
    ],
  )

  const userList = useUserList(
    JSON.stringify([
      adminConnection.baseUrl,
      adminConnection.apiKey,
      connection.accountId,
    ]),
  )
  const [searchQuery, setSearchQuery] = React.useState('')
  React.useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(userList.search), 250)
    return () => clearTimeout(timer)
  }, [userList.search])
  const usersQuery = useQuery({
    enabled:
      canManageUsers &&
      Boolean(connection.accountId) &&
      searchQuery === userList.search,
    ...adminUsersQuery(adminConnection, {
      page: userList.page,
      pageSize: userList.pageSize,
      search: searchQuery,
    }),
  })
  const memberships = useUserGroupMemberships(
    adminConnection,
    canManageUsers && Boolean(connection.accountId),
  )
  const users = usersQuery.data?.users ?? []
  const total = usersQuery.data?.total ?? 0
  const accountTotal = usersQuery.data?.accountTotal ?? 0
  const managerCount = usersQuery.data?.managerCount ?? 0
  const pageCount = Math.max(1, Math.ceil(total / userList.pageSize))
  React.useEffect(() => {
    if (usersQuery.isSuccess && userList.page > pageCount)
      userList.setPage(pageCount)
  }, [usersQuery.isSuccess, userList, pageCount])

  const createUser = useMutation({
    mutationFn: (input: CreateUserInput) =>
      createAdminUser(adminConnection, input),
    onError: (error) => showErrorToast(error),
    onSuccess: async (result) => {
      setGeneratedCredential(result)
      setAddUserOpen(false)
      toast.success(t('toast.userCreated'))
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
    },
  })

  const regenerateKey = useMutation({
    mutationFn: (user: AdminUser) =>
      regenerateAdminUserKey(adminConnection, user.accountId, user.userId),
    onError: (error) => showErrorToast(error),
    onSuccess: async (result, user) => {
      setGeneratedCredential(result)
      setPendingRegenerateUser(null)
      if (
        user.accountId === connection.accountId &&
        user.userId === connection.userId &&
        result.apiKey
      ) {
        await switchIdentity({
          accountId: user.accountId,
          allowLegacyIdentityFallback: true,
          apiKey: result.apiKey,
          userId: user.userId,
          rememberWorkbench:
            !workbenchIdentity ||
            (workbenchIdentity.accountId === user.accountId &&
              workbenchIdentity.userId === user.userId),
        })
      }
      toast.success(t('toast.keyRegenerated'))
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
    },
  })

  const updateRole = useMutation({
    mutationFn: (input: UpdateUserRoleInput) =>
      updateAdminUserRole(adminConnection, input),
    onError: (error) => showErrorToast(error),
    onSuccess: async (_, input) => {
      setPendingRoleChange(null)
      toast.success(
        t('toast.roleUpdated', {
          role: t(`roles.${input.role}`),
          user: input.userId,
        }),
      )
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
    },
  })

  const removeUser = useMutation({
    mutationFn: (user: AdminUser) =>
      removeAdminUser(adminConnection, user.accountId, user.userId),
    onError: (error) => showErrorToast(error),
    onSuccess: async (_, user) => {
      setPendingRemoveUser(null)
      toast.success(t('toast.userRemoved', { user: user.userId }))
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
    },
  })

  async function copyKey(value: string | undefined): Promise<void> {
    if (!value) {
      return
    }
    try {
      await copyTextToClipboard(value)
      toast.success(t('toast.copied'))
    } catch {
      toast.error(t('toast.copyFailed'))
    }
  }

  async function useUserIdentity(user: AdminUser | KeyResult): Promise<void> {
    if (serverMode !== 'trusted' && !user.apiKey) {
      toast.error(t('management.noUsableKey'))
      return
    }
    const accountId = user.accountId || connection.accountId
    const userId = user.userId || connection.userId
    const identityKey = `${accountId}:${userId}`
    setSwitchingIdentityKey(identityKey)
    try {
      await switchIdentity({
        accountId,
        allowLegacyIdentityFallback: true,
        apiKey: user.apiKey || '',
        userId,
        preserveProfile: true,
      })
      await navigate({ to: '/home' })
      toast.success(t('toast.dataKeySelected'))
    } catch (error) {
      showErrorToast(error)
    } finally {
      setSwitchingIdentityKey('')
    }
  }

  if (isConnectionRoleLoading) {
    return (
      <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
        <LoaderCircleIcon className="size-4 animate-spin" />
        {t('loading')}
      </div>
    )
  }

  if (!canManageUsers) {
    return (
      <Card className="mx-auto mt-10 w-full max-w-xl">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl border bg-muted/40 text-muted-foreground">
            <ShieldAlertIcon className="size-5" />
          </div>
          <CardTitle>{t('management.accessDeniedTitle')}</CardTitle>
          <CardDescription>
            {t('management.accessDeniedDescription')}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center">
          <Button nativeButton={false} render={<Link to="/admin/settings" />}>
            <KeyRoundIcon />
            {t('management.openConnection')}
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="users-member-panel">
      <div className="users-toolbar">
        <div className="users-toolbar-navigation">
          <UserSpaceSwitcher canManageAccounts={canManageAccounts} />
          {navigation}
        </div>
        <div className="users-toolbar-actions">
          <div className="users-search">
            <SearchIcon aria-hidden="true" />
            <Input
              type="search"
              aria-label={t('userList.search')}
              placeholder={t('appShell:userManagement.search')}
              value={userList.search}
              onChange={(event) => userList.setSearch(event.target.value)}
            />
          </div>
          <Button
            type="button"
            className="users-add-button"
            onClick={() => setAddUserOpen(true)}
          >
            <PlusIcon />
            {t('appShell:userManagement.addMember')}
          </Button>
        </div>
      </div>

      <RootSetupGuide onCreate={() => setAddUserOpen(true)} />

      <Card
        className="users-member-table gap-0 overflow-hidden py-0"
        aria-label={t('management.memberListTitle')}
      >
        <CardContent className="p-0">
          {usersQuery.isPending || searchQuery !== userList.search ? (
            <div className="flex min-h-56 items-center justify-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" />
              {t('loading')}
            </div>
          ) : usersQuery.isError ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="font-medium">{t('empty.adminTitle')}</p>
              <ErrorMessage error={usersQuery.error} className="max-w-lg" />
            </div>
          ) : total === 0 ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="font-medium">
                {t(accountTotal ? 'userList.noResults' : 'empty.usersTitle')}
              </p>
              <p className="text-sm text-muted-foreground">
                {t(
                  accountTotal
                    ? 'userList.noResultsDescription'
                    : 'empty.usersDescription',
                )}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/20 hover:bg-muted/20">
                    <TableHead>{t('appShell:userManagement.userId')}</TableHead>
                    <TableHead>
                      {t('appShell:userManagement.apiKeys')}
                    </TableHead>
                    <TableHead>{t('memoryPolicy.title')}</TableHead>
                    <TableHead>{t('table.groups')}</TableHead>
                    <TableHead>{t('table.actions')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => {
                    const identityKey = `${user.accountId}:${user.userId}`
                    const userGroups = memberships.byUser.get(user.userId) ?? []
                    const isCurrentIdentity =
                      (Boolean(connection.apiKey) ||
                        serverMode === 'trusted') &&
                      user.accountId === connection.accountId &&
                      user.userId === connection.userId
                    const canSwitchIdentity =
                      !isCurrentIdentity &&
                      (serverMode === 'trusted' || Boolean(user.apiKey))
                    const isLastManager =
                      (user.role === 'admin' || user.role === 'root') &&
                      managerCount <= 1
                    const removeDisabled =
                      isCurrentIdentity || isLastManager || removeUser.isPending
                    const removeDisabledReason = isCurrentIdentity
                      ? t('management.cannotRemoveCurrentIdentity')
                      : isLastManager
                        ? t('management.cannotRemoveLastManager')
                        : t('actions.removeUser', { user: user.userId })

                    return (
                      <TableRow
                        key={identityKey}
                        className={
                          isCurrentIdentity ? 'bg-primary/[0.025]' : ''
                        }
                      >
                        <TableCell>
                          <div className="users-user-identity">
                            <span
                              aria-hidden="true"
                              className="users-user-avatar"
                            >
                              {Array.from(user.userId).slice(0, 2).join('')}
                            </span>
                            <span className="truncate" title={user.userId}>
                              {user.userId}
                            </span>
                            {isCurrentIdentity ? (
                              <span className="sr-only">
                                {t('actions.currentIdentity')}
                              </span>
                            ) : null}
                            {user.role === 'admin' || user.role === 'root' ? (
                              <Badge
                                variant="outline"
                                className="users-admin-badge"
                              >
                                {t(`roles.${user.role}`)}
                              </Badge>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          <UserApiKeyCell
                            key={`${adminConnection.baseUrl}:${user.apiKey ?? user.keyPrefix}`}
                            user={user}
                            onCopy={(value) => void copyKey(value)}
                          />
                        </TableCell>
                        <TableCell>
                          <UserMemoryPolicyCell
                            connection={adminConnection}
                            user={user}
                          />
                        </TableCell>
                        <TableCell>
                          {memberships.status === 'loading' ? (
                            <span className="text-muted-foreground">
                              {t('loading')}
                            </span>
                          ) : memberships.status === 'error' ? (
                            <details className="text-muted-foreground">
                              <summary className="cursor-pointer">
                                {t('groups.loadFailed')}
                              </summary>
                              {memberships.errors.map(({ groupId, error }) => (
                                <p
                                  key={groupId ?? 'groups'}
                                  className="mt-1 break-all text-xs"
                                >
                                  {groupId ? `${groupId}: ` : ''}
                                  {getErrorMessage(error)}
                                </p>
                              ))}
                            </details>
                          ) : userGroups.length === 0 ? (
                            <span className="text-muted-foreground">—</span>
                          ) : (
                            <div
                              className="flex max-w-56 items-center gap-1.5"
                              title={userGroups.join(', ')}
                            >
                              {userGroups.slice(0, 2).map((groupId) => (
                                <Badge
                                  key={groupId}
                                  variant="secondary"
                                  className="max-w-24 min-w-0"
                                >
                                  <span className="truncate">{groupId}</span>
                                </Badge>
                              ))}
                              {userGroups.length > 2 && (
                                <Badge variant="outline">
                                  +{userGroups.length - 2}
                                </Badge>
                              )}
                            </div>
                          )}
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  aria-label={t(
                                    'appShell:userManagement.userActions',
                                    { user: user.userId },
                                  )}
                                />
                              }
                            >
                              <EllipsisIcon />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-48">
                              {canSwitchIdentity ? (
                                <DropdownMenuItem
                                  disabled={Boolean(switchingIdentityKey)}
                                  onClick={() => void useUserIdentity(user)}
                                >
                                  <KeyRoundIcon />
                                  {t('actions.switchIdentity')}
                                </DropdownMenuItem>
                              ) : null}
                              {user.role === 'user' || user.role === 'admin' ? (
                                <DropdownMenuItem
                                  disabled={updateRole.isPending}
                                  onClick={() =>
                                    setPendingRoleChange({
                                      accountId: user.accountId,
                                      userId: user.userId,
                                      role:
                                        user.role === 'admin'
                                          ? 'user'
                                          : 'admin',
                                    })
                                  }
                                >
                                  {t(
                                    user.role === 'admin'
                                      ? 'appShell:userManagement.makeMember'
                                      : 'appShell:userManagement.makeAdmin',
                                  )}
                                </DropdownMenuItem>
                              ) : null}
                              {serverMode !== 'trusted' ? (
                                <DropdownMenuItem
                                  disabled={
                                    regenerateKey.isPending ||
                                    Boolean(switchingIdentityKey)
                                  }
                                  onClick={() => setPendingRegenerateUser(user)}
                                >
                                  <RotateCwIcon />
                                  {t('actions.regenerate')}
                                </DropdownMenuItem>
                              ) : null}
                              <DropdownMenuItem
                                variant="destructive"
                                disabled={removeDisabled}
                                title={removeDisabledReason}
                                onClick={() => setPendingRemoveUser(user)}
                              >
                                <Trash2Icon />
                                {t('actions.removeUser', { user: user.userId })}
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              {total > userList.pageSize ? (
                <UserPagination
                  {...userList}
                  total={total}
                  pageCount={pageCount}
                />
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>

      <AddUserDialog
        open={addUserOpen}
        onOpenChange={setAddUserOpen}
        accountId={connection.accountId}
        isPending={createUser.isPending}
        onCreate={(input) => createUser.mutate(input)}
      />

      <AlertDialog
        open={Boolean(pendingRegenerateUser)}
        onOpenChange={(open) => {
          if (!open) {
            setPendingRegenerateUser(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('dialogs.regenerate.title')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('dialogs.regenerate.description', {
                account: pendingRegenerateUser?.accountId,
                user: pendingRegenerateUser?.userId,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('actions.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingRegenerateUser) {
                  regenerateKey.mutate(pendingRegenerateUser)
                }
              }}
              disabled={regenerateKey.isPending}
            >
              <RotateCwIcon />
              {t('actions.regenerate')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={Boolean(pendingRoleChange)}
        onOpenChange={(open) => {
          if (!open && !updateRole.isPending) {
            setPendingRoleChange(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('dialogs.changeRole.title')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('dialogs.changeRole.description', {
                account: pendingRoleChange?.accountId,
                role: pendingRoleChange
                  ? t(`roles.${pendingRoleChange.role}`)
                  : '',
                user: pendingRoleChange?.userId,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={updateRole.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={updateRole.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (pendingRoleChange) {
                  updateRole.mutate(pendingRoleChange)
                }
              }}
            >
              {updateRole.isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : null}
              {t('actions.confirmRoleChange')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={Boolean(pendingRemoveUser)}
        onOpenChange={(open) => {
          if (!open && !removeUser.isPending) {
            setPendingRemoveUser(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('dialogs.removeUser.title')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('dialogs.removeUser.description', {
                account: pendingRemoveUser?.accountId,
                user: pendingRemoveUser?.userId,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeUser.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={removeUser.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (pendingRemoveUser) {
                  removeUser.mutate(pendingRemoveUser)
                }
              }}
            >
              {removeUser.isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <Trash2Icon />
              )}
              {t('actions.confirmRemoveUser')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
