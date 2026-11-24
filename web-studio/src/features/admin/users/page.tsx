import { ErrorMessage } from '#/shared/components/error-message'
import { showErrorToast } from '#/shared/lib/error-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import {
  CheckIcon,
  CopyIcon,
  KeyRoundIcon,
  LoaderCircleIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ShieldAlertIcon,
  Trash2Icon,
  UsersRoundIcon,
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
  fetchAdminUsersPage,
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
import { DeleteAccountButton } from '#/features/admin/users/components/delete-account-button'
import { UserMemoryPolicyCell } from '#/features/admin/users/components/user-memory-policy-cell'
import { UserRoleCell } from '#/features/admin/users/components/user-role-cell'
import { getErrorMessage } from '#/shared/lib/error'

function maskApiKey(value: string | undefined): string {
  if (!value) {
    return '-'
  }
  if (value.length <= 16) {
    return value
  }
  return `${value.slice(0, 10)}...${value.slice(-6)}`
}

function resolveKeyLabel(user: AdminUser): string {
  return user.apiKey ? maskApiKey(user.apiKey) : user.keyPrefix || '-'
}

export function UserManagementRoute() {
  return <Outlet />
}

export function UserManagementPanel() {
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
    queryFn: () =>
      fetchAdminUsersPage(adminConnection, connection.accountId, {
        page: userList.page,
        pageSize: userList.pageSize,
        search: searchQuery,
      }),
    queryKey: [
      'managed-users',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      connection.accountId,
      userList.page,
      userList.pageSize,
      searchQuery,
    ],
    retry: false,
  })
  const memberships = useUserGroupMemberships(
    adminConnection,
    canManageUsers && Boolean(connection.accountId),
  )
  const users = usersQuery.data?.users ?? []
  const total = usersQuery.data?.total ?? 0
  const accountTotal = usersQuery.data?.accountTotal ?? 0
  const managerCount = usersQuery.data?.managerCount ?? 0
  const visibleKeys = usersQuery.data?.keyCount ?? 0
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
      await queryClient.invalidateQueries({ queryKey: ['account-switcher'] })
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
      await queryClient.invalidateQueries({ queryKey: ['account-switcher'] })
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
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex min-w-0 flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('management.title')}
          </h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('management.currentAccountDescription')}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {canManageAccounts ? (
            <DeleteAccountButton
              accountId={connection.accountId}
              adminConnection={adminConnection}
            />
          ) : null}
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void usersQuery.refetch()
              void queryClient.invalidateQueries({
                queryKey: [
                  'managed-groups',
                  adminConnection.baseUrl,
                  adminConnection.accountId,
                  adminConnection.apiKey,
                ],
              })
              void queryClient.invalidateQueries({
                queryKey: [
                  'managed-group-members',
                  adminConnection.baseUrl,
                  adminConnection.accountId,
                  adminConnection.apiKey,
                ],
              })
              void queryClient.invalidateQueries({
                queryKey: [
                  'user-memory-settings',
                  adminConnection.baseUrl,
                  adminConnection.apiKey,
                  connection.accountId,
                ],
              })
            }}
            disabled={usersQuery.isFetching}
          >
            <RefreshCwIcon
              className={usersQuery.isFetching ? 'animate-spin' : undefined}
            />
            {t('actions.refresh')}
          </Button>
          <Button type="button" onClick={() => setAddUserOpen(true)}>
            <PlusIcon />
            {t('actions.addUser')}
          </Button>
        </div>
      </header>

      <RootSetupGuide onCreate={() => setAddUserOpen(true)} />

      <div className="grid gap-3 sm:grid-cols-2">
        <Card className="bg-card/70 py-4">
          <CardContent className="flex items-center justify-between gap-4 px-5">
            <div>
              <p className="text-sm text-muted-foreground">
                {t('stats.users')}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {usersQuery.isSuccess ? accountTotal : '-'}
              </p>
            </div>
            <div className="flex size-10 items-center justify-center rounded-md border bg-background/70 text-primary">
              <UsersRoundIcon className="size-4" />
            </div>
          </CardContent>
        </Card>
        <Card className="bg-card/70 py-4">
          <CardContent className="flex items-center justify-between gap-4 px-5">
            <div>
              <p className="text-sm text-muted-foreground">
                {t('stats.apiKeys')}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {usersQuery.isSuccess ? visibleKeys : '-'}
              </p>
            </div>
            <div className="flex size-10 items-center justify-center rounded-md border bg-background/70 text-primary">
              <KeyRoundIcon className="size-4" />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="border-b bg-muted/20">
          <CardTitle>{t('management.memberListTitle')}</CardTitle>
          <CardDescription>
            {t(
              canManageAccounts
                ? 'management.memberListDescriptionRoot'
                : 'management.memberListDescription',
            )}
          </CardDescription>
          <Input
            type="search"
            className="max-w-sm"
            aria-label={t('userList.search')}
            placeholder={t('userList.search')}
            value={userList.search}
            onChange={(event) => userList.setSearch(event.target.value)}
          />
        </CardHeader>
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
                    <TableHead>{t('table.user')}</TableHead>
                    <TableHead>{t('table.role')}</TableHead>
                    <TableHead>{t('table.groups')}</TableHead>
                    <TableHead>{t('memoryPolicy.title')}</TableHead>
                    <TableHead>{t('table.apiKey')}</TableHead>
                    <TableHead className="text-right">
                      {t('table.actions')}
                    </TableHead>
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
                    const isSwitching = switchingIdentityKey === identityKey
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
                        <TableCell className="font-medium">
                          <div className="group/user flex items-center gap-2">
                            <span className="truncate">{user.userId}</span>
                            {isCurrentIdentity ? (
                              <Badge
                                variant="secondary"
                                className="gap-1 font-normal"
                              >
                                <CheckIcon />
                                {t('actions.currentIdentity')}
                              </Badge>
                            ) : null}
                            {canSwitchIdentity ? (
                              <Button
                                type="button"
                                variant="secondary"
                                size="xs"
                                disabled={Boolean(switchingIdentityKey)}
                                className={
                                  isSwitching
                                    ? 'opacity-100'
                                    : 'opacity-0 transition-opacity group-hover/user:opacity-100 group-focus-within/user:opacity-100 [@media(hover:none)]:opacity-100'
                                }
                                onClick={() => void useUserIdentity(user)}
                              >
                                {isSwitching ? (
                                  <LoaderCircleIcon className="animate-spin" />
                                ) : (
                                  <KeyRoundIcon />
                                )}
                                {t('actions.switchIdentity')}
                              </Button>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          <UserRoleCell
                            userId={user.userId}
                            role={user.role}
                            editable={canManageUsers}
                            pending={updateRole.isPending}
                            onChange={(role) =>
                              setPendingRoleChange({
                                accountId: user.accountId,
                                userId: user.userId,
                                role,
                              })
                            }
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
                            <span className="text-muted-foreground">
                              {t('groups.notJoined')}
                            </span>
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
                          <UserMemoryPolicyCell
                            connection={adminConnection}
                            user={user}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex min-w-0 items-center gap-1">
                            <code className="max-w-[20rem] truncate rounded-md border bg-muted/40 px-2 py-1 font-mono text-xs">
                              {resolveKeyLabel(user)}
                            </code>
                            {user.apiKey ? (
                              <Tooltip>
                                <TooltipTrigger
                                  render={
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon-xs"
                                      aria-label={t('actions.copy')}
                                      onClick={() => void copyKey(user.apiKey)}
                                    />
                                  }
                                >
                                  <CopyIcon />
                                </TooltipTrigger>
                                <TooltipContent>
                                  {t('actions.copy')}
                                </TooltipContent>
                              </Tooltip>
                            ) : null}
                            {serverMode !== 'trusted' && (
                              <div className="ml-1 flex items-center border-l border-border/70 pl-1.5">
                                <Tooltip>
                                  <TooltipTrigger
                                    render={
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon-xs"
                                        aria-label={t('actions.regenerate')}
                                        onClick={() =>
                                          setPendingRegenerateUser(user)
                                        }
                                        disabled={
                                          regenerateKey.isPending ||
                                          Boolean(switchingIdentityKey)
                                        }
                                      />
                                    }
                                  >
                                    <RotateCwIcon />
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    {t('actions.regenerate')}
                                  </TooltipContent>
                                </Tooltip>
                              </div>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center justify-end gap-1">
                            <Tooltip>
                              <TooltipTrigger
                                render={
                                  <span
                                    className="inline-flex"
                                    title={removeDisabledReason}
                                  />
                                }
                              >
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon-sm"
                                  disabled={removeDisabled}
                                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                  aria-label={t('actions.removeUser', {
                                    user: user.userId,
                                  })}
                                  onClick={() => setPendingRemoveUser(user)}
                                >
                                  <Trash2Icon />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>
                                {removeDisabledReason}
                              </TooltipContent>
                            </Tooltip>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              <UserPagination
                {...userList}
                total={total}
                pageCount={pageCount}
              />
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
