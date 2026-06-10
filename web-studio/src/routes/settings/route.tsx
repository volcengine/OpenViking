import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import {
  CircleAlertIcon,
  CircleDashedIcon,
  CopyIcon,
  DatabaseIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ServerIcon,
  ShieldCheckIcon,
  UserRoundIcon,
  UsersRoundIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

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
import { Field, FieldContent, FieldLabel } from '#/components/ui/field'
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
import { copyTextToClipboard } from '#/lib/clipboard'
import { cn } from '#/lib/utils'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

import { AccountSelect, UserSelect } from '#/components/identity-select'
import {
  DEFAULT_ACCOUNT_ID,
  DEFAULT_USER_ID,
  sortedAccountIds,
  sortedAccounts,
} from '#/lib/admin-options'
import {
  createAdminAccount,
  createAdminUser,
  fetchAdminAccounts,
  fetchAdminUsers,
  probeStudioConnection,
  regenerateAdminUserKey,
} from '#/lib/admin'
import type {
  AdminConnection,
  AdminAccount,
  AdminUser,
  CapabilityProbeResult,
  CreateAccountInput,
  CreateUserInput,
  KeyResult,
} from '#/lib/admin'

export const Route = createFileRoute('/settings')({
  component: SettingsRoute,
})

const USER_ROLES = ['user', 'admin'] as const

type AddAccountDraft = CreateAccountInput
type AddUserDraft = CreateUserInput

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
}) {
  return (
    <Card className="bg-card/70 py-4">
      <CardContent className="flex items-center justify-between gap-4 px-5">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        </div>
        <div className="flex size-10 items-center justify-center rounded-md border bg-background/70 text-primary">
          {icon}
        </div>
      </CardContent>
    </Card>
  )
}

function KeyResultCard({
  onClear,
  onUseForData,
  result,
}: {
  onClear: () => void
  onUseForData: () => void
  result: KeyResult
}) {
  const { t } = useTranslation('settings')

  if (!result.apiKey) {
    return null
  }

  async function copyKey(): Promise<void> {
    try {
      await copyTextToClipboard(result.apiKey)
      toast.success(t('toast.copied'))
    } catch {
      toast.error(t('toast.copyFailed'))
    }
  }

  return (
    <Card className="border-primary/20 bg-primary/5 py-4">
      <CardContent className="grid gap-3 px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-medium">{t('keyResult.title')}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {t('keyResult.description')}
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClear}>
            {t('keyResult.dismiss')}
          </Button>
        </div>
        <div className="flex min-w-0 flex-col gap-2 rounded-md border bg-background/80 p-3 sm:flex-row sm:items-center">
          <code className="min-w-0 flex-1 truncate font-mono text-sm">
            {result.apiKey}
          </code>
          <Button type="button" variant="outline" size="sm" onClick={copyKey}>
            <CopyIcon />
            {t('actions.copy')}
          </Button>
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onUseForData}
          >
            <DatabaseIcon />
            {t('actions.useForData')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function getCapabilityIcon(result: CapabilityProbeResult | undefined) {
  if (!result) {
    return <CircleDashedIcon className="size-4" />
  }
  if (result.state === 'ok') {
    return <ShieldCheckIcon className="size-4" />
  }
  if (result.state === 'error') {
    return <CircleAlertIcon className="size-4" />
  }
  return <CircleDashedIcon className="size-4" />
}

function CapabilityStatus({
  isLoading,
  label,
  result,
}: {
  isLoading: boolean
  label: string
  result: CapabilityProbeResult | undefined
}) {
  const { t } = useTranslation('settings')
  const state = isLoading ? 'checking' : result?.state || 'skipped'

  return (
    <div
      className={cn(
        'flex min-w-0 items-start gap-2 rounded-md border bg-background/70 px-3 py-2 text-sm',
        state === 'ok' && 'border-emerald-500/35 text-emerald-700',
        state === 'error' && 'border-destructive/35 text-destructive',
      )}
    >
      <div className={cn('mt-0.5', isLoading && 'animate-spin')}>
        {getCapabilityIcon(result)}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium">{label}</span>
          <span className="text-xs text-muted-foreground">
            {t(`health.state.${state}`)}
          </span>
        </div>
        {result?.detail ? (
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {result.detail}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function AccountFilterChips({
  accounts,
  disabled,
  label,
  onChange,
  selectedAccountIds,
}: {
  accounts: readonly AdminAccount[]
  disabled?: boolean
  label: string
  onChange: (value: string[]) => void
  selectedAccountIds: readonly string[]
}) {
  const options = sortedAccountIds(
    accounts.map((account) => account.accountId),
    selectedAccountIds[0] || DEFAULT_ACCOUNT_ID,
  )
  const selected = new Set(selectedAccountIds)

  function toggle(accountId: string): void {
    if (disabled) {
      return
    }
    if (selected.has(accountId)) {
      const next = selectedAccountIds.filter((item) => item !== accountId)
      if (next.length > 0) {
        onChange(next)
      }
      return
    }
    onChange([...selectedAccountIds, accountId])
  }

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex max-w-full flex-wrap gap-2">
        {options.map((accountId) => {
          const isSelected = selected.has(accountId)

          return (
            <button
              key={accountId}
              type="button"
              aria-pressed={isSelected}
              disabled={disabled}
              onClick={() => toggle(accountId)}
              className={cn(
                'h-8 rounded-full border px-3 font-mono text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                isSelected
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground',
              )}
            >
              {accountId}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function UserApiKeyInput({
  accountId,
  disabled,
  id,
  onChange,
  placeholder,
  userId,
  value,
}: {
  accountId: string
  disabled?: boolean
  id: string
  onChange: (value: string) => void
  placeholder: string
  userId: string
  value: string
}) {
  const identity = `${accountId || DEFAULT_ACCOUNT_ID}/${userId || DEFAULT_USER_ID}`

  return (
    <div
      className={cn(
        'flex h-9 w-full min-w-0 items-center gap-2 rounded-md border border-input bg-transparent bg-clip-padding px-2.5 shadow-xs transition-[color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <span className="shrink-0 rounded-sm bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
        [{identity}]
      </span>
      <input
        id={id}
        type="password"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
      />
    </div>
  )
}

function AddAccountDialog({
  isPending,
  onCreate,
  onOpenChange,
  open,
}: {
  isPending: boolean
  onCreate: (draft: AddAccountDraft) => void
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useTranslation('settings')
  const [draft, setDraft] = React.useState<AddAccountDraft>({
    accountId: '',
    adminUserId: DEFAULT_USER_ID,
  })

  React.useEffect(() => {
    if (open) {
      setDraft({ accountId: '', adminUserId: DEFAULT_USER_ID })
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('dialogs.addAccount.title')}</DialogTitle>
          <DialogDescription>
            {t('dialogs.addAccount.description')}
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            onCreate(draft)
          }}
        >
          <Field>
            <FieldLabel htmlFor="settings-add-account-id">
              {t('fields.account')}
            </FieldLabel>
            <FieldContent>
              <Input
                id="settings-add-account-id"
                value={draft.accountId}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    accountId: event.target.value,
                  }))
                }
                placeholder={t('placeholders.account')}
                required
              />
            </FieldContent>
          </Field>
          <Field>
            <FieldLabel htmlFor="settings-add-account-admin">
              {t('fields.adminUser')}
            </FieldLabel>
            <FieldContent>
              <Input
                id="settings-add-account-admin"
                value={draft.adminUserId}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    adminUserId: event.target.value,
                  }))
                }
                placeholder={DEFAULT_USER_ID}
                required
              />
            </FieldContent>
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button type="submit" disabled={isPending}>
              <PlusIcon />
              {t('actions.addAccount')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function AddUserDialog({
  accounts,
  defaultAccountId,
  isPending,
  onCreate,
  onOpenChange,
  open,
}: {
  accounts: readonly AdminAccount[]
  defaultAccountId: string
  isPending: boolean
  onCreate: (draft: AddUserDraft) => void
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useTranslation('settings')
  const [draft, setDraft] = React.useState<AddUserDraft>({
    accountId: defaultAccountId || DEFAULT_ACCOUNT_ID,
    role: 'user',
    userId: '',
  })

  React.useEffect(() => {
    if (open) {
      setDraft({
        accountId: defaultAccountId || DEFAULT_ACCOUNT_ID,
        role: 'user',
        userId: '',
      })
    }
  }, [defaultAccountId, open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('dialogs.addUser.title')}</DialogTitle>
          <DialogDescription>
            {t('dialogs.addUser.description')}
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            onCreate(draft)
          }}
        >
          <Field>
            <FieldLabel>{t('fields.account')}</FieldLabel>
            <FieldContent>
              <AccountSelect
                accounts={accounts}
                label={t('fields.account')}
                value={draft.accountId}
                onChange={(accountId) =>
                  setDraft((current) => ({ ...current, accountId }))
                }
              />
            </FieldContent>
          </Field>
          <Field>
            <FieldLabel htmlFor="settings-add-user-id">
              {t('fields.user')}
            </FieldLabel>
            <FieldContent>
              <Input
                id="settings-add-user-id"
                value={draft.userId}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    userId: event.target.value,
                  }))
                }
                placeholder={t('placeholders.user')}
                required
              />
            </FieldContent>
          </Field>
          <Field>
            <FieldLabel>{t('fields.role')}</FieldLabel>
            <FieldContent>
              <Select
                value={draft.role}
                onValueChange={(role) => {
                  if (role) {
                    setDraft((current) => ({ ...current, role }))
                  }
                }}
              >
                <SelectTrigger
                  aria-label={t('fields.role')}
                  className="h-9 w-full"
                >
                  <SelectValue>{t(`roles.${draft.role}`)}</SelectValue>
                </SelectTrigger>
                <SelectContent alignItemWithTrigger>
                  {USER_ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {t(`roles.${role}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldContent>
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button type="submit" disabled={isPending}>
              <PlusIcon />
              {t('actions.addUser')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SettingsRoute() {
  const { t } = useTranslation('settings')
  const queryClient = useQueryClient()
  const { connection, saveConnection, serverMode } = useAppConnection()
  const [draft, setDraft] = React.useState<ConnectionDraft>(connection)
  const [managedAccountIds, setManagedAccountIds] = React.useState<string[]>([
    connection.accountId || DEFAULT_ACCOUNT_ID,
  ])
  const [addAccountOpen, setAddAccountOpen] = React.useState(false)
  const [addUserOpen, setAddUserOpen] = React.useState(false)
  const [keyResult, setKeyResult] = React.useState<KeyResult | null>(null)
  const [pendingRegenerateUser, setPendingRegenerateUser] =
    React.useState<AdminUser | null>(null)

  React.useEffect(() => {
    setDraft(connection)
  }, [connection])

  const controlApiKey = draft.adminApiKey.trim() || draft.apiKey.trim()
  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: draft.accountId || DEFAULT_ACCOUNT_ID,
      apiKey: controlApiKey,
      baseUrl: draft.baseUrl,
      userId: draft.userId || DEFAULT_USER_ID,
    }),
    [controlApiKey, draft.accountId, draft.baseUrl, draft.userId],
  )

  const probeQuery = useQuery({
    enabled: Boolean(draft.baseUrl) && serverMode !== 'checking',
    queryFn: () =>
      probeStudioConnection({
        accountId: draft.accountId || DEFAULT_ACCOUNT_ID,
        adminApiKey: draft.adminApiKey,
        apiKey: draft.apiKey,
        baseUrl: draft.baseUrl,
        serverMode,
        userId: draft.userId || DEFAULT_USER_ID,
      }),
    queryKey: [
      'studio-connection-probe',
      draft.baseUrl,
      draft.adminApiKey,
      draft.apiKey,
      draft.accountId,
      draft.userId,
      serverMode,
    ],
    retry: false,
    staleTime: 15_000,
  })

  const hasAdminAccess =
    serverMode !== 'dev' && probeQuery.data?.admin.state === 'ok'
  const hasSavedAdminApiKey = Boolean(draft.adminApiKey.trim())
  const showAdminCredentialFields =
    serverMode !== 'dev' && (hasAdminAccess || hasSavedAdminApiKey)
  const canQueryAdmin = Boolean(controlApiKey) && hasAdminAccess
  const showDevApiKeyPlaceholder = serverMode === 'dev'
  const shouldPromoteApiKeyToAdmin =
    hasAdminAccess && !draft.adminApiKey.trim() && Boolean(draft.apiKey.trim())

  React.useEffect(() => {
    if (!hasAdminAccess || draft.adminApiKey.trim() || !draft.apiKey.trim()) {
      return
    }

    const next = {
      ...draft,
      adminApiKey: draft.apiKey,
      apiKey: '',
    }
    setDraft(next)
    saveConnection(next)
  }, [draft, hasAdminAccess, saveConnection])

  const accountsQuery = useQuery({
    enabled: canQueryAdmin,
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

  const selectedAccountId = draft.accountId || DEFAULT_ACCOUNT_ID
  const selectedManagedAccountIds = React.useMemo(() => {
    const selected = sortedAccountIds(managedAccountIds, '')
    return selected.length > 0
      ? selected
      : sortedAccountIds([selectedAccountId], '')
  }, [managedAccountIds, selectedAccountId])

  const usersQuery = useQuery({
    enabled: canQueryAdmin && Boolean(selectedAccountId),
    queryFn: () => fetchAdminUsers(adminConnection, selectedAccountId),
    queryKey: [
      'admin-users',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      adminConnection.accountId,
      adminConnection.userId,
      selectedAccountId,
    ],
    retry: false,
  })

  const managedUsersQuery = useQuery({
    enabled: canQueryAdmin && selectedManagedAccountIds.length > 0,
    queryFn: async () => {
      const usersByAccount = await Promise.all(
        selectedManagedAccountIds.map((accountId) =>
          fetchAdminUsers(adminConnection, accountId),
        ),
      )
      return usersByAccount.flat()
    },
    queryKey: [
      'admin-users',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      adminConnection.accountId,
      adminConnection.userId,
      'managed',
      selectedManagedAccountIds,
    ],
    retry: false,
  })

  const accountOptions = React.useMemo<AdminAccount[]>(() => {
    if (accountsQuery.data) {
      return sortedAccounts(accountsQuery.data)
    }

    return sortedAccounts(
      selectedManagedAccountIds.map((accountId) => ({
        accountId,
        userCount:
          managedUsersQuery.data?.filter((user) => user.accountId === accountId)
            .length ?? 0,
      })),
    )
  }, [accountsQuery.data, managedUsersQuery.data, selectedManagedAccountIds])

  React.useEffect(() => {
    if (!accountOptions.length) {
      return
    }

    const accountIds = accountOptions.map((account) => account.accountId)
    const preferred = accountIds.includes(DEFAULT_ACCOUNT_ID)
      ? DEFAULT_ACCOUNT_ID
      : accountIds[0]

    setDraft((current) => {
      if (current.accountId && accountIds.includes(current.accountId)) {
        return current
      }

      const next = { ...current, accountId: preferred }
      saveConnection(next)
      return next
    })
    setManagedAccountIds((current) => {
      const next = current.filter((accountId) => accountIds.includes(accountId))
      return next.length > 0 ? next : [preferred]
    })
  }, [accountOptions, saveConnection])

  React.useEffect(() => {
    const users = usersQuery.data
    if (!users?.length) {
      return
    }

    const userIds = users.map((user) => user.userId)
    const preferred = userIds.includes(DEFAULT_USER_ID)
      ? DEFAULT_USER_ID
      : userIds[0]
    setDraft((current) => {
      const hasCurrentUser =
        Boolean(current.userId) && userIds.includes(current.userId)
      const userId = hasCurrentUser ? current.userId : preferred
      const selectedUser = users.find((user) => user.userId === userId)
      const apiKey =
        selectedUser?.apiKey || (hasCurrentUser ? current.apiKey : '')
      const next = { ...current, apiKey, userId }

      if (next.apiKey !== current.apiKey || next.userId !== current.userId) {
        saveConnection(next)
        return next
      }

      return current
    })
  }, [saveConnection, usersQuery.data])

  const createAccountMutation = useMutation({
    mutationFn: (input: CreateAccountInput) =>
      createAdminAccount(adminConnection, input),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async (result, variables) => {
      const keyResultWithIdentity = {
        ...result,
        accountId: result.accountId || variables.accountId,
        userId: result.userId || variables.adminUserId,
      }
      setKeyResult(keyResultWithIdentity)
      setManagedAccountIds([variables.accountId])
      if (keyResultWithIdentity.apiKey) {
        const next = buildDataKeyConnection(draft, keyResultWithIdentity)
        setDraft(next)
        saveConnection(next)
      } else {
        const next = {
          ...draft,
          accountId: variables.accountId,
          userId: variables.adminUserId,
        }
        setDraft(next)
        saveConnection(next)
      }
      setAddAccountOpen(false)
      toast.success(t('toast.accountCreated'))
      await queryClient.invalidateQueries({ queryKey: ['admin-accounts'] })
      await queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const createUserMutation = useMutation({
    mutationFn: (input: CreateUserInput) =>
      createAdminUser(adminConnection, input),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async (result, variables) => {
      const keyResultWithIdentity = {
        ...result,
        accountId: result.accountId || variables.accountId,
        userId: result.userId || variables.userId,
      }
      setKeyResult(keyResultWithIdentity)
      setManagedAccountIds((current) =>
        sortedAccountIds([...current, variables.accountId], ''),
      )
      if (keyResultWithIdentity.apiKey) {
        const next = buildDataKeyConnection(draft, keyResultWithIdentity)
        setDraft(next)
        saveConnection(next)
      } else {
        const next = {
          ...draft,
          accountId: variables.accountId,
          userId: variables.userId,
        }
        setDraft(next)
        saveConnection(next)
      }
      setAddUserOpen(false)
      toast.success(t('toast.userCreated'))
      await queryClient.invalidateQueries({ queryKey: ['admin-accounts'] })
      await queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const regenerateMutation = useMutation({
    mutationFn: (user: AdminUser) =>
      regenerateAdminUserKey(adminConnection, user.accountId, user.userId),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async (result, user) => {
      setKeyResult(result)
      if (
        result.apiKey &&
        user.accountId === draft.accountId &&
        user.userId === draft.userId
      ) {
        const next = buildDataKeyConnection(draft, result)
        setDraft(next)
        saveConnection(next)
      }
      setPendingRegenerateUser(null)
      toast.success(t('toast.keyRegenerated'))
      await queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const users = usersQuery.data ?? []
  const managedUsers = managedUsersQuery.data ?? []
  const totalAccounts = accountOptions.length
  const totalUsers =
    accountOptions.reduce((sum, account) => sum + account.userCount, 0) ||
    managedUsers.length
  const visibleKeys = managedUsers.filter(
    (user) => user.apiKey || user.keyPrefix,
  ).length
  const adminUnavailable = !canQueryAdmin || managedUsersQuery.isError

  function updateDraft(next: Partial<ConnectionDraft>): void {
    const updated = { ...draft, ...next }
    setDraft(updated)
    saveConnection(updated)
  }

  function updateConnectionAccount(accountId: string): void {
    updateDraft({
      accountId,
      apiKey: '',
      userId: DEFAULT_USER_ID,
    })
  }

  function updateConnectionUser(userId: string): void {
    const selectedUser = users.find((user) => user.userId === userId)
    updateDraft({
      apiKey: selectedUser?.apiKey || '',
      userId,
    })
  }

  function buildDataKeyConnection(
    current: ConnectionDraft,
    result: KeyResult,
  ): ConnectionDraft {
    const nextApiKey = result.apiKey || current.apiKey
    return {
      ...current,
      accountId: result.accountId || current.accountId || DEFAULT_ACCOUNT_ID,
      adminApiKey:
        current.adminApiKey ||
        (current.apiKey && current.apiKey !== nextApiKey ? current.apiKey : ''),
      apiKey: nextApiKey,
      userId: result.userId || current.userId || DEFAULT_USER_ID,
    }
  }

  function useKeyForData(result: KeyResult): void {
    if (!result.apiKey) {
      return
    }
    const next = buildDataKeyConnection(draft, result)
    setDraft(next)
    saveConnection(next)
    toast.success(t('toast.dataKeySelected'))
  }

  async function refreshAdmin(): Promise<void> {
    await Promise.all([
      accountsQuery.refetch(),
      usersQuery.refetch(),
      managedUsersQuery.refetch(),
      probeQuery.refetch(),
    ])
  }

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

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t('page.title')}
        </h1>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          {t('page.description')}
        </p>
      </header>

      <Card className="gap-0 overflow-hidden border-primary/25 bg-primary/[0.025] py-0 shadow-sm ring-1 ring-primary/10">
        <CardHeader className="gap-2 border-b border-primary/15 bg-primary/[0.07] px-5 py-3.5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <KeyRoundIcon className="size-4" />
                </div>
                <CardTitle>{t('connection.title')}</CardTitle>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 px-5 py-4">
          <div
            className={cn(
              'grid gap-3',
              hasAdminAccess
                ? 'xl:grid-cols-[minmax(16rem,1.2fr)_minmax(12rem,0.8fr)_minmax(12rem,0.8fr)]'
                : 'xl:grid-cols-[minmax(16rem,1.2fr)_minmax(16rem,1fr)]',
            )}
          >
            <Field>
              <FieldLabel htmlFor="settings-base-url">
                {t('fields.baseUrl')}
              </FieldLabel>
              <FieldContent>
                <Input
                  id="settings-base-url"
                  value={draft.baseUrl}
                  onChange={(event) =>
                    updateDraft({ baseUrl: event.target.value })
                  }
                  placeholder={t('placeholders.baseUrl')}
                />
              </FieldContent>
            </Field>
            {hasAdminAccess ? (
              <>
                <Field>
                  <FieldLabel>{t('fields.account')}</FieldLabel>
                  <FieldContent>
                    <AccountSelect
                      accounts={accountOptions}
                      disabled={!canQueryAdmin || accountsQuery.isLoading}
                      label={t('fields.account')}
                      value={draft.accountId || DEFAULT_ACCOUNT_ID}
                      onChange={updateConnectionAccount}
                    />
                  </FieldContent>
                </Field>
                <Field>
                  <FieldLabel>{t('fields.user')}</FieldLabel>
                  <FieldContent>
                    <UserSelect
                      disabled={!canQueryAdmin || usersQuery.isLoading}
                      label={t('fields.user')}
                      users={users}
                      value={draft.userId || DEFAULT_USER_ID}
                      onChange={updateConnectionUser}
                    />
                  </FieldContent>
                </Field>
              </>
            ) : showDevApiKeyPlaceholder ? (
              <Field>
                <FieldLabel htmlFor="settings-api-key-dev">
                  {t('fields.apiKey')}
                </FieldLabel>
                <FieldContent>
                  <Input
                    id="settings-api-key-dev"
                    value={t('placeholders.devModeApiKey')}
                    disabled
                    readOnly
                  />
                </FieldContent>
              </Field>
            ) : showAdminCredentialFields ? null : (
              <Field>
                <FieldLabel htmlFor="settings-api-key">
                  {t('fields.apiKey')}
                </FieldLabel>
                <FieldContent>
                  <Input
                    id="settings-api-key"
                    type="password"
                    value={draft.apiKey}
                    onChange={(event) =>
                      updateDraft({ apiKey: event.target.value })
                    }
                    placeholder={t('placeholders.apiKey')}
                  />
                </FieldContent>
              </Field>
            )}
          </div>
          {showAdminCredentialFields ? (
            <>
              <div className="grid gap-3">
                <Field>
                  <FieldLabel htmlFor="settings-admin-api-key">
                    {t('fields.adminApiKey')}
                  </FieldLabel>
                  <FieldContent>
                    <Input
                      id="settings-admin-api-key"
                      type="password"
                      value={
                        shouldPromoteApiKeyToAdmin
                          ? draft.apiKey
                          : draft.adminApiKey
                      }
                      onChange={(event) =>
                        updateDraft({ adminApiKey: event.target.value })
                      }
                      placeholder={t('placeholders.adminApiKey')}
                    />
                  </FieldContent>
                </Field>
                <Field>
                  <FieldLabel htmlFor="settings-user-api-key">
                    {t('fields.userApiKey')}
                  </FieldLabel>
                  <FieldContent>
                    <UserApiKeyInput
                      accountId={draft.accountId || DEFAULT_ACCOUNT_ID}
                      id="settings-user-api-key"
                      userId={draft.userId || DEFAULT_USER_ID}
                      value={shouldPromoteApiKeyToAdmin ? '' : draft.apiKey}
                      onChange={(apiKey) => updateDraft({ apiKey })}
                      placeholder={t('placeholders.userApiKey')}
                    />
                  </FieldContent>
                </Field>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <CapabilityStatus
                  isLoading={probeQuery.isFetching}
                  label={t('health.admin')}
                  result={probeQuery.data?.admin}
                />
                <CapabilityStatus
                  isLoading={probeQuery.isFetching}
                  label={t('health.data')}
                  result={probeQuery.data?.data}
                />
              </div>
              {accountsQuery.isError && managedUsersQuery.isError ? (
                <p className="text-sm text-destructive">
                  {t('connection.adminError', {
                    message: getErrorMessage(accountsQuery.error),
                  })}
                </p>
              ) : accountsQuery.isError ? (
                <p className="text-sm text-muted-foreground">
                  {t('connection.accountListLimited')}
                </p>
              ) : null}
            </>
          ) : null}
        </CardContent>
      </Card>

      {hasAdminAccess ? (
        <div className="grid gap-3 md:grid-cols-3">
          <StatCard
            label={t('stats.accounts')}
            value={totalAccounts || '-'}
            icon={<ServerIcon className="size-4" />}
          />
          <StatCard
            label={t('stats.users')}
            value={totalUsers || '-'}
            icon={<UsersRoundIcon className="size-4" />}
          />
          <StatCard
            label={t('stats.apiKeys')}
            value={visibleKeys || '-'}
            icon={<KeyRoundIcon className="size-4" />}
          />
        </div>
      ) : null}

      {hasAdminAccess && keyResult ? (
        <KeyResultCard
          result={keyResult}
          onClear={() => setKeyResult(null)}
          onUseForData={() => useKeyForData(keyResult)}
        />
      ) : null}

      {hasAdminAccess ? (
        <Card className="overflow-hidden">
          <CardHeader className="gap-4 border-b bg-muted/20">
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
              <div className="min-w-0">
                <CardTitle>{t('management.title')}</CardTitle>
                <CardDescription>{t('management.description')}</CardDescription>
              </div>
              <div className="flex flex-wrap items-end gap-2 lg:justify-end">
                <AccountFilterChips
                  accounts={accountOptions}
                  disabled={!canQueryAdmin || accountsQuery.isLoading}
                  label={t('management.accountFilter')}
                  selectedAccountIds={selectedManagedAccountIds}
                  onChange={setManagedAccountIds}
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void refreshAdmin()}
                  disabled={!canQueryAdmin || managedUsersQuery.isFetching}
                >
                  <RefreshCwIcon
                    className={cn(
                      managedUsersQuery.isFetching && 'animate-spin',
                    )}
                  />
                  {t('actions.refresh')}
                </Button>
                <Button
                  type="button"
                  onClick={() => setAddAccountOpen(true)}
                  disabled={!canQueryAdmin}
                >
                  <PlusIcon />
                  {t('actions.addAccount')}
                </Button>
                <Button
                  type="button"
                  onClick={() => setAddUserOpen(true)}
                  disabled={!canQueryAdmin}
                >
                  <PlusIcon />
                  {t('actions.addUser')}
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {adminUnavailable ? (
              <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
                <div className="flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground">
                  <KeyRoundIcon className="size-5" />
                </div>
                <p className="font-medium">{t('empty.adminTitle')}</p>
                <p className="max-w-lg text-sm text-muted-foreground">
                  {t('empty.adminDescription')}
                </p>
              </div>
            ) : managedUsersQuery.isLoading ? (
              <div className="flex min-h-56 items-center justify-center text-sm text-muted-foreground">
                {t('loading')}
              </div>
            ) : managedUsers.length === 0 ? (
              <div className="flex min-h-56 flex-col items-center justify-center gap-2 px-6 text-center">
                <div className="flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground">
                  <UserRoundIcon className="size-5" />
                </div>
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
                      <TableHead>{t('table.account')}</TableHead>
                      <TableHead>{t('table.user')}</TableHead>
                      <TableHead>{t('table.role')}</TableHead>
                      <TableHead>{t('table.apiKey')}</TableHead>
                      <TableHead className="text-right">
                        {t('table.actions')}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {managedUsers.map((user) => (
                      <TableRow key={`${user.accountId}:${user.userId}`}>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {user.accountId}
                        </TableCell>
                        <TableCell className="font-medium">
                          {user.userId}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              user.role === 'admin' ? 'secondary' : 'outline'
                            }
                          >
                            {t(`roles.${user.role}`, {
                              defaultValue: user.role,
                            })}
                          </Badge>
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
                            {user.apiKey ? (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() =>
                                  useKeyForData({
                                    accountId: user.accountId,
                                    apiKey: user.apiKey || '',
                                    userId: user.userId,
                                  })
                                }
                              >
                                <DatabaseIcon />
                                {t('actions.useForData')}
                              </Button>
                            ) : null}
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setPendingRegenerateUser(user)}
                              disabled={regenerateMutation.isPending}
                            >
                              <RotateCwIcon />
                              {t('actions.regenerate')}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}

      <AddAccountDialog
        open={addAccountOpen}
        onOpenChange={setAddAccountOpen}
        isPending={createAccountMutation.isPending}
        onCreate={(next) => createAccountMutation.mutate(next)}
      />
      <AddUserDialog
        open={addUserOpen}
        onOpenChange={setAddUserOpen}
        accounts={accountOptions}
        defaultAccountId={selectedManagedAccountIds[0] || selectedAccountId}
        isPending={createUserMutation.isPending}
        onCreate={(next) => createUserMutation.mutate(next)}
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
                  regenerateMutation.mutate(pendingRegenerateUser)
                }
              }}
              disabled={regenerateMutation.isPending}
            >
              <RotateCwIcon />
              {t('actions.regenerate')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
