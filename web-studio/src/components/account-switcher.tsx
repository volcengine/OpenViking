import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Building2Icon,
  CheckIcon,
  ChevronDownIcon,
  LoaderCircleIcon,
  PlusIcon,
  SearchIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '#/components/ui/popover'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  createAdminAccount,
  fetchAdminAccounts,
  fetchAdminUsers,
} from '#/lib/admin'
import type {
  AdminConnection,
  AdminUser,
  CreateAccountInput,
} from '#/lib/admin'
import { DEFAULT_USER_ID } from '#/lib/admin-options'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'
import { cn } from '#/lib/utils'

const PLAIN_INPUT_PROPS = {
  autoCapitalize: 'none',
  autoComplete: 'off',
  autoCorrect: 'off',
  spellCheck: false,
} as const

export function selectAccountUser(
  users: readonly AdminUser[],
  currentUserId: string,
  requireApiKey: boolean,
): AdminUser | undefined {
  const candidates = requireApiKey
    ? users.filter((user) => Boolean(user.apiKey))
    : [...users]
  return (
    candidates.find((user) => user.userId === currentUserId) ??
    candidates.find((user) => user.userId === DEFAULT_USER_ID) ??
    candidates.find((user) => user.role === 'admin') ??
    candidates[0]
  )
}

export function AccountSwitcher() {
  const { t } = useTranslation('accountSwitcher')
  const queryClient = useQueryClient()
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    switchIdentity,
  } = useAppConnection()
  const [open, setOpen] = React.useState(false)
  const [createOpen, setCreateOpen] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const [switchingAccountId, setSwitchingAccountId] = React.useState('')
  const [manualSwitch, setManualSwitch] = React.useState<{
    accountId: string
    apiKey: string
  } | null>(null)
  const [createDraft, setCreateDraft] = React.useState<CreateAccountInput>({
    accountId: '',
    adminUserId: DEFAULT_USER_ID,
  })

  const { canManageAccounts } = resolveStudioManagementCapabilities({
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

  const accountsQuery = useQuery({
    enabled: canManageAccounts && open,
    queryFn: () => fetchAdminAccounts(adminConnection),
    queryKey: [
      'account-switcher',
      adminConnection.baseUrl,
      adminConnection.apiKey,
    ],
    retry: false,
  })

  const filteredAccounts = React.useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase()
    const accounts = accountsQuery.data ?? []
    if (!normalizedSearch) {
      return accounts
    }
    return accounts.filter((account) =>
      account.accountId.toLowerCase().includes(normalizedSearch),
    )
  }, [accountsQuery.data, search])

  async function selectAccount(accountId: string): Promise<void> {
    if (accountId === connection.accountId) {
      setOpen(false)
      return
    }

    setSwitchingAccountId(accountId)
    try {
      const users = await fetchAdminUsers(adminConnection, accountId)
      const user = selectAccountUser(
        users,
        connection.userId,
        serverMode === 'api_key',
      )
      if (!user) {
        if (serverMode === 'api_key') {
          setOpen(false)
          setManualSwitch({ accountId, apiKey: '' })
          return
        }
        throw new Error(t('errors.noUsers'))
      }
      await switchIdentity({
        accountId,
        apiKey: user.apiKey || '',
        userId: user.userId,
      })
      setOpen(false)
      toast.success(
        t('toast.switched', {
          account: accountId,
        }),
      )
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSwitchingAccountId('')
    }
  }

  const createAccount = useMutation({
    mutationFn: (input: CreateAccountInput) =>
      createAdminAccount(adminConnection, input),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : String(error)),
    onSuccess: async (result, input) => {
      if (!result.apiKey && serverMode === 'api_key') {
        toast.error(t('errors.noCreatedKey'))
        return
      }
      try {
        await queryClient.invalidateQueries({ queryKey: ['account-switcher'] })
        await switchIdentity({
          accountId: result.accountId || input.accountId,
          apiKey: result.apiKey,
          userId: result.userId || input.adminUserId,
        })
        setCreateOpen(false)
        setOpen(false)
        setCreateDraft({ accountId: '', adminUserId: DEFAULT_USER_ID })
        toast.success(t('toast.created', { account: input.accountId }))
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      }
    },
  })

  const accountLabel = connection.accountId || t('unset')

  if (!canManageAccounts) {
    return (
      <div
        className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left group-data-[collapsible=icon]:size-9 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
        title={accountLabel}
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-sidebar-border bg-sidebar-accent/50">
          <Building2Icon className="size-4" />
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold group-data-[collapsible=icon]:hidden">
          {accountLabel}
        </span>
      </div>
    )
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring group-data-[collapsible=icon]:size-9 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-sidebar-border bg-sidebar-accent/50">
            <Building2Icon className="size-4" />
          </span>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold group-data-[collapsible=icon]:hidden">
            {accountLabel}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground group-data-[collapsible=icon]:hidden" />
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={6}
          className="w-64 gap-0 p-0"
        >
          <div className="border-b p-2">
            <div className="relative">
              <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={t('searchPlaceholder')}
                className="h-8 pl-8 text-sm"
              />
            </div>
          </div>
          <div className="max-h-60 overflow-y-auto p-1.5">
            {accountsQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 px-2.5 py-6 text-sm text-muted-foreground">
                <LoaderCircleIcon className="size-3.5 animate-spin" />
                {t('loading')}
              </div>
            ) : accountsQuery.isError ? (
              <div className="grid gap-1 px-2.5 py-5 text-center">
                <p className="text-sm font-medium text-destructive">
                  {t('errors.loadAccounts')}
                </p>
                <p className="line-clamp-2 text-xs text-muted-foreground">
                  {accountsQuery.error instanceof Error
                    ? accountsQuery.error.message
                    : String(accountsQuery.error)}
                </p>
              </div>
            ) : filteredAccounts.length === 0 ? (
              <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">
                {t('empty')}
              </p>
            ) : (
              filteredAccounts.map((account) => {
                const active = account.accountId === connection.accountId
                const switching = switchingAccountId === account.accountId
                return (
                  <button
                    key={account.accountId}
                    type="button"
                    disabled={Boolean(switchingAccountId)}
                    onClick={() => void selectAccount(account.accountId)}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent disabled:cursor-wait disabled:opacity-60',
                      active && 'bg-accent/70',
                    )}
                  >
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-md border bg-background">
                      <Building2Icon className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {account.accountId}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {t('memberCount', { count: account.userCount })}
                      </span>
                    </span>
                    {switching ? (
                      <LoaderCircleIcon className="size-3.5 animate-spin" />
                    ) : active ? (
                      <CheckIcon className="size-3.5 text-primary" />
                    ) : null}
                  </button>
                )
              })
            )}
          </div>
          <div className="border-t p-1.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="w-full justify-start px-2.5"
              onClick={() => {
                setOpen(false)
                setCreateOpen(true)
              }}
            >
              <PlusIcon />
              {t('create')}
            </Button>
          </div>
        </PopoverContent>
      </Popover>

      <Dialog
        open={Boolean(manualSwitch)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && !switchingAccountId) {
            setManualSwitch(null)
          }
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (!manualSwitch?.apiKey.trim()) {
                return
              }
              const target = manualSwitch
              setSwitchingAccountId(target.accountId)
              void switchIdentity({
                accountId: target.accountId,
                apiKey: target.apiKey,
                userId: '',
              })
                .then(() => {
                  toast.success(
                    t('toast.switched', { account: target.accountId }),
                  )
                  setManualSwitch(null)
                })
                .catch((error: unknown) => {
                  toast.error(
                    error instanceof Error ? error.message : String(error),
                  )
                })
                .finally(() => setSwitchingAccountId(''))
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('manualSwitch.title')}</DialogTitle>
              <DialogDescription>
                {t('manualSwitch.description', {
                  account: manualSwitch?.accountId,
                })}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 py-5">
              <label className="grid gap-2 text-sm font-medium">
                {t('manualSwitch.keyLabel')}
                <Input
                  required
                  type="password"
                  value={manualSwitch?.apiKey ?? ''}
                  onChange={(event) =>
                    setManualSwitch((current) =>
                      current
                        ? { ...current, apiKey: event.target.value }
                        : current,
                    )
                  }
                  placeholder={t('manualSwitch.keyPlaceholder')}
                  {...PLAIN_INPUT_PROPS}
                />
              </label>
              <p className="text-xs leading-5 text-muted-foreground">
                {t('manualSwitch.hint')}
              </p>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={Boolean(switchingAccountId)}
                onClick={() => setManualSwitch(null)}
              >
                {t('dialog.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={
                  Boolean(switchingAccountId) || !manualSwitch?.apiKey.trim()
                }
              >
                {switchingAccountId ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <CheckIcon />
                )}
                {t('manualSwitch.submit')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              createAccount.mutate(createDraft)
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('dialog.title')}</DialogTitle>
              <DialogDescription>{t('dialog.description')}</DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-5">
              <label className="grid gap-2 text-sm font-medium">
                {t('dialog.accountLabel')}
                <Input
                  required
                  value={createDraft.accountId}
                  onChange={(event) =>
                    setCreateDraft((current) => ({
                      ...current,
                      accountId: event.target.value,
                    }))
                  }
                  placeholder={t('dialog.accountPlaceholder')}
                  {...PLAIN_INPUT_PROPS}
                />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                {t('dialog.adminLabel')}
                <Input
                  required
                  value={createDraft.adminUserId}
                  onChange={(event) =>
                    setCreateDraft((current) => ({
                      ...current,
                      adminUserId: event.target.value,
                    }))
                  }
                  placeholder={DEFAULT_USER_ID}
                  {...PLAIN_INPUT_PROPS}
                />
              </label>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
              >
                {t('dialog.cancel')}
              </Button>
              <Button type="submit" disabled={createAccount.isPending}>
                {createAccount.isPending ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <PlusIcon />
                )}
                {t('dialog.submit')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
