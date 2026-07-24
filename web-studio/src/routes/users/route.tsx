import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  CheckIcon,
  CopyIcon,
  KeyRoundIcon,
  LoaderCircleIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ShieldAlertIcon,
  UsersRoundIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  createAdminUser,
  fetchAdminUsers,
  regenerateAdminUserKey,
  updateAdminUserRole,
} from '#/lib/admin'
import type {
  AdminConnection,
  AdminUser,
  AdminUserRole,
  CreateUserInput,
  KeyResult,
  UpdateUserRoleInput,
} from '#/lib/admin'
import { copyTextToClipboard } from '#/lib/clipboard'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'

export const Route = createFileRoute('/users')({
  component: UserManagementRoute,
})

const PLAIN_INPUT_PROPS = {
  autoCapitalize: 'none',
  autoComplete: 'off',
  autoCorrect: 'off',
  spellCheck: false,
} as const

const USER_ROLE_OPTIONS: AdminUserRole[] = ['user', 'admin', 'root']

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function isAdminUserRole(role: string): role is AdminUserRole {
  return USER_ROLE_OPTIONS.includes(role as AdminUserRole)
}

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

function UserManagementRoute() {
  const { t } = useTranslation('settings')
  const queryClient = useQueryClient()
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    switchIdentity,
  } = useAppConnection()
  const [addUserOpen, setAddUserOpen] = React.useState(false)
  const [keyResult, setKeyResult] = React.useState<KeyResult | null>(null)
  const [pendingRegenerateUser, setPendingRegenerateUser] =
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

  const usersQuery = useQuery({
    enabled: canManageUsers && Boolean(connection.accountId),
    queryFn: () => fetchAdminUsers(adminConnection, connection.accountId),
    queryKey: [
      'managed-users',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      connection.accountId,
    ],
    retry: false,
  })

  const createUser = useMutation({
    mutationFn: (input: CreateUserInput) =>
      createAdminUser(adminConnection, input),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async (result) => {
      setKeyResult(result)
      setAddUserOpen(false)
      toast.success(t('toast.userCreated'))
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
      await queryClient.invalidateQueries({ queryKey: ['account-switcher'] })
    },
  })

  const regenerateKey = useMutation({
    mutationFn: (user: AdminUser) =>
      regenerateAdminUserKey(adminConnection, user.accountId, user.userId),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async (result, user) => {
      setKeyResult(result)
      setPendingRegenerateUser(null)
      if (
        user.accountId === connection.accountId &&
        user.userId === connection.userId &&
        result.apiKey
      ) {
        await switchIdentity({
          accountId: user.accountId,
          apiKey: result.apiKey,
          userId: user.userId,
        })
      }
      toast.success(t('toast.keyRegenerated'))
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
    },
  })

  const updateRole = useMutation({
    mutationFn: (input: UpdateUserRoleInput) =>
      updateAdminUserRole(adminConnection, input),
    onError: (error) => toast.error(getErrorMessage(error)),
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
    if (!user.apiKey) {
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
        apiKey: user.apiKey,
        userId,
      })
      toast.success(t('toast.dataKeySelected'))
    } catch (error) {
      toast.error(getErrorMessage(error))
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
          <Button nativeButton={false} render={<Link to="/settings" />}>
            <KeyRoundIcon />
            {t('management.openConnection')}
          </Button>
        </CardContent>
      </Card>
    )
  }

  const users = usersQuery.data ?? []
  const visibleKeys = users.filter(
    (user) => user.apiKey || user.keyPrefix,
  ).length
  const keyResultIsCurrentIdentity =
    Boolean(keyResult?.accountId && keyResult.userId) &&
    keyResult?.accountId === connection.accountId &&
    keyResult.userId === connection.userId
  const keyResultIdentityKey = `${keyResult?.accountId || connection.accountId}:${keyResult?.userId || connection.userId}`

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">
              {t('management.title')}
            </h1>
            <Badge variant="secondary" className="max-w-72 truncate font-mono">
              {connection.accountId}
            </Badge>
          </div>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('management.currentAccountDescription', {
              account: connection.accountId,
            })}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => void usersQuery.refetch()}
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

      <div className="grid gap-3 sm:grid-cols-2">
        <Card className="bg-card/70 py-4">
          <CardContent className="flex items-center justify-between gap-4 px-5">
            <div>
              <p className="text-sm text-muted-foreground">
                {t('stats.users')}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {users.length || '-'}
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
                {visibleKeys || '-'}
              </p>
            </div>
            <div className="flex size-10 items-center justify-center rounded-md border bg-background/70 text-primary">
              <KeyRoundIcon className="size-4" />
            </div>
          </CardContent>
        </Card>
      </div>

      {keyResult?.apiKey ? (
        <Card className="border-primary/20 bg-primary/5 py-4">
          <CardContent className="grid gap-3 px-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-medium">{t('keyResult.title')}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t('keyResult.description')}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setKeyResult(null)}
              >
                {t('keyResult.dismiss')}
              </Button>
            </div>
            <div className="flex min-w-0 flex-col gap-2 rounded-md border bg-background/80 p-3 sm:flex-row sm:items-center">
              <code className="min-w-0 flex-1 truncate font-mono text-sm">
                {keyResult.apiKey}
              </code>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void copyKey(keyResult.apiKey)}
              >
                <CopyIcon />
                {t('actions.copy')}
              </Button>
              {keyResultIsCurrentIdentity ? (
                <Badge
                  variant="secondary"
                  className="h-8 gap-1.5 px-3 font-normal"
                >
                  <CheckIcon />
                  {t('actions.currentIdentity')}
                </Badge>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  disabled={Boolean(switchingIdentityKey)}
                  onClick={() => void useUserIdentity(keyResult)}
                >
                  {switchingIdentityKey === keyResultIdentityKey ? (
                    <LoaderCircleIcon className="animate-spin" />
                  ) : (
                    <KeyRoundIcon />
                  )}
                  {t('actions.switchIdentity')}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      ) : null}

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
        </CardHeader>
        <CardContent className="p-0">
          {usersQuery.isLoading ? (
            <div className="flex min-h-56 items-center justify-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" />
              {t('loading')}
            </div>
          ) : usersQuery.isError ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="font-medium">{t('empty.adminTitle')}</p>
              <p className="max-w-lg text-sm text-muted-foreground">
                {getErrorMessage(usersQuery.error)}
              </p>
            </div>
          ) : users.length === 0 ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="font-medium">{t('empty.usersTitle')}</p>
              <p className="text-sm text-muted-foreground">
                {t('empty.usersDescription')}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/20 hover:bg-muted/20">
                    <TableHead>{t('table.user')}</TableHead>
                    <TableHead>{t('table.role')}</TableHead>
                    <TableHead>{t('table.apiKey')}</TableHead>
                    <TableHead className="text-right">
                      {t('table.actions')}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => {
                    const identityKey = `${user.accountId}:${user.userId}`
                    const isCurrentIdentity =
                      user.accountId === connection.accountId &&
                      user.userId === connection.userId
                    const isSwitching =
                      switchingIdentityKey === identityKey

                    return (
                      <TableRow
                        key={identityKey}
                        className={isCurrentIdentity ? 'bg-primary/[0.025]' : ''}
                      >
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-2">
                            {user.userId}
                            {isCurrentIdentity ? (
                              <Badge
                                variant="secondary"
                                className="gap-1 font-normal"
                              >
                                <CheckIcon />
                                {t('actions.currentIdentity')}
                              </Badge>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          {canManageAccounts &&
                          isAdminUserRole(user.role) ? (
                            <Select
                              value={user.role}
                              disabled={updateRole.isPending}
                              onValueChange={(role) => {
                                if (
                                  role &&
                                  isAdminUserRole(role) &&
                                  role !== user.role
                                ) {
                                  setPendingRoleChange({
                                    accountId: user.accountId,
                                    role,
                                    userId: user.userId,
                                  })
                                }
                              }}
                            >
                              <SelectTrigger
                                className="h-8 w-28"
                                aria-label={t('actions.changeRole', {
                                  user: user.userId,
                                })}
                              >
                                <SelectValue>
                                  {t(`roles.${user.role}`)}
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent>
                                {USER_ROLE_OPTIONS.map((role) => (
                                  <SelectItem key={role} value={role}>
                                    {t(`roles.${role}`)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <Badge
                              variant={
                                user.role === 'admin'
                                  ? 'secondary'
                                  : 'outline'
                              }
                            >
                              {t(`roles.${user.role}`, {
                                defaultValue: user.role,
                              })}
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex min-w-0 items-center gap-2">
                            <code className="max-w-[20rem] truncate rounded-md border bg-muted/40 px-2 py-1 font-mono text-xs">
                              {resolveKeyLabel(user)}
                            </code>
                            {user.apiKey ? (
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-xs"
                                aria-label={t('actions.copy')}
                                onClick={() => void copyKey(user.apiKey)}
                              >
                                <CopyIcon />
                              </Button>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-2">
                            {user.apiKey && !isCurrentIdentity ? (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                disabled={Boolean(switchingIdentityKey)}
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
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setPendingRegenerateUser(user)}
                              disabled={
                                regenerateKey.isPending ||
                                Boolean(switchingIdentityKey)
                              }
                            >
                              <RotateCwIcon />
                              {t('actions.regenerate')}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
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
            <AlertDialogTitle>
              {t('dialogs.changeRole.title')}
            </AlertDialogTitle>
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
    </div>
  )
}

function AddUserDialog({
  accountId,
  isPending,
  onCreate,
  onOpenChange,
  open,
}: {
  accountId: string
  isPending: boolean
  onCreate: (draft: CreateUserInput) => void
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useTranslation('settings')
  const [draft, setDraft] = React.useState<CreateUserInput>({
    accountId,
    role: 'user',
    userId: '',
  })

  React.useEffect(() => {
    if (open) {
      setDraft({ accountId, role: 'user', userId: '' })
    }
  }, [accountId, open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            onCreate(draft)
          }}
        >
          <DialogHeader>
            <DialogTitle>{t('dialogs.addUser.title')}</DialogTitle>
            <DialogDescription>
              {t('dialogs.addUser.currentAccountDescription', { accountId })}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-5">
            <label className="grid gap-2 text-sm font-medium">
              {t('fields.user')}
              <Input
                required
                value={draft.userId}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    userId: event.target.value,
                  }))
                }
                placeholder={t('placeholders.user')}
                {...PLAIN_INPUT_PROPS}
              />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              {t('fields.role')}
              <Select
                value={draft.role}
                onValueChange={(role) =>
                  setDraft((current) => ({
                    ...current,
                    role: role || 'user',
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">{t('roles.user')}</SelectItem>
                  <SelectItem value="admin">{t('roles.admin')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <PlusIcon />
              )}
              {t('actions.addUser')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
