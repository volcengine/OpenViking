import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Building2Icon,
  CheckIcon,
  ChevronDownIcon,
  LoaderCircleIcon,
  UserRoundIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '#/components/ui/popover'
import { useAppConnection } from '#/hooks/use-app-connection'
import { fetchAdminUsers } from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'

export function getUserInitial(userId: string): string {
  const normalizedUserId = userId.trim()
  return normalizedUserId ? normalizedUserId.slice(0, 1).toUpperCase() : '?'
}

export function CurrentUserMenu() {
  const { t } = useTranslation('appShell')
  const { connection, serverMode, switchIdentity } = useAppConnection()
  const [open, setOpen] = React.useState(false)
  const [manualUserId, setManualUserId] = React.useState('')
  const [switchingUserId, setSwitchingUserId] = React.useState('')
  const { accountId, userId } = connection
  const accountLabel = accountId || t('header.currentUser.unset')
  const userLabel = userId || t('header.currentUser.unset')
  const canSwitchUser = serverMode === 'trusted' && Boolean(accountId)
  const canListUsers = Boolean(connection.adminApiKey)
  const manualTargetUserId = manualUserId.trim()
  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId,
      apiKey: connection.adminApiKey,
      baseUrl: connection.baseUrl,
      userId,
    }),
    [accountId, connection.adminApiKey, connection.baseUrl, userId],
  )
  const usersQuery = useQuery({
    enabled: canSwitchUser && canListUsers && open,
    queryFn: () => fetchAdminUsers(adminConnection, accountId),
    queryKey: [
      'current-user-menu',
      adminConnection.baseUrl,
      adminConnection.apiKey,
      accountId,
    ],
    retry: false,
  })

  async function selectUser(nextUserId: string): Promise<void> {
    const normalizedUserId = nextUserId.trim()
    if (!normalizedUserId || normalizedUserId === userId) {
      return
    }

    setSwitchingUserId(normalizedUserId)
    try {
      await switchIdentity({
        accountId,
        allowLegacyIdentityFallback: true,
        apiKey: '',
        userId: normalizedUserId,
      })
      setManualUserId('')
      setOpen(false)
      toast.success(t('header.currentUser.switchSuccess'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSwitchingUserId('')
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('header.currentUser.openMenu', { user: userLabel })}
        className="group flex h-10 max-w-52 items-center gap-2 rounded-2xl border border-border/80 bg-muted/60 p-1 pr-2.5 text-left shadow-xs outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-foreground text-xs font-semibold text-background shadow-sm">
          {getUserInitial(userLabel)}
        </span>
        <span className="hidden min-w-0 flex-1 sm:block">
          <span className="block truncate text-xs font-semibold leading-4 text-foreground">
            {userLabel}
          </span>
          <span className="block truncate text-[10px] leading-3 text-muted-foreground">
            {t('header.currentUser.accountSummary', {
              account: accountLabel,
            })}
          </span>
        </span>
        <ChevronDownIcon className="hidden size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 sm:block" />
      </PopoverTrigger>

      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={8}
        className="w-72 gap-0 overflow-hidden p-0"
      >
        <div className="flex items-center gap-3 border-b bg-muted/35 px-4 py-3.5">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-foreground text-sm font-semibold text-background shadow-sm">
            {getUserInitial(userLabel)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{userLabel}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {t('header.currentUser.signedInAs')}
            </div>
          </div>
        </div>

        <dl className="space-y-1 p-2">
          <div className="flex items-center gap-3 rounded-lg px-2.5 py-2">
            <Building2Icon className="size-4 shrink-0 text-muted-foreground" />
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">
              {t('header.currentUser.account')}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-right text-xs font-medium">
              {accountLabel}
            </dd>
          </div>
          <div className="flex items-center gap-3 rounded-lg px-2.5 py-2">
            <UserRoundIcon className="size-4 shrink-0 text-muted-foreground" />
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">
              {t('header.currentUser.user')}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-right text-xs font-medium">
              {userLabel}
            </dd>
          </div>
        </dl>

        {canSwitchUser ? (
          <div className="border-t p-1.5">
            <p className="px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
              {t('header.currentUser.switchUser')}
            </p>
            <div className="max-h-56 overflow-y-auto">
              {!canListUsers ? (
                <form
                  className="grid gap-2 p-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void selectUser(manualTargetUserId)
                  }}
                >
                  <label className="sr-only" htmlFor="trusted-user-id">
                    {t('header.currentUser.userId')}
                  </label>
                  <input
                    id="trusted-user-id"
                    type="text"
                    autoComplete="off"
                    value={manualUserId}
                    className="h-9 rounded-md border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                    placeholder={t('header.currentUser.userIdPlaceholder')}
                    onChange={(event) => setManualUserId(event.target.value)}
                  />
                  <button
                    type="submit"
                    disabled={
                      !manualTargetUserId ||
                      manualTargetUserId === userId ||
                      Boolean(switchingUserId)
                    }
                    className="flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {switchingUserId ? (
                      <LoaderCircleIcon className="size-3.5 animate-spin" />
                    ) : null}
                    {t('header.currentUser.switchAction')}
                  </button>
                </form>
              ) : usersQuery.isLoading ? (
                <div className="flex items-center justify-center gap-2 px-3 py-5 text-xs text-muted-foreground">
                  <LoaderCircleIcon className="size-3.5 animate-spin" />
                  {t('header.currentUser.loadingUsers')}
                </div>
              ) : usersQuery.isError ? (
                <div className="grid gap-2 px-2.5 py-3 text-center">
                  <p className="text-xs text-destructive">
                    {t('header.currentUser.loadUsersFailed')}
                  </p>
                  <button
                    type="button"
                    className="text-xs font-medium text-primary hover:underline"
                    onClick={() => void usersQuery.refetch()}
                  >
                    {t('header.currentUser.retry')}
                  </button>
                </div>
              ) : usersQuery.data?.length ? (
                usersQuery.data.map((user) => {
                  const current = user.userId === userId
                  const switching = switchingUserId === user.userId
                  return (
                    <button
                      key={user.userId}
                      type="button"
                      aria-current={current ? 'true' : undefined}
                      aria-label={user.userId}
                      disabled={current || Boolean(switchingUserId)}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent disabled:cursor-default disabled:opacity-70"
                      onClick={() => void selectUser(user.userId)}
                    >
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md border bg-background text-xs font-semibold">
                        {getUserInitial(user.userId)}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {user.userId}
                      </span>
                      {switching ? (
                        <LoaderCircleIcon className="size-3.5 animate-spin" />
                      ) : current ? (
                        <CheckIcon className="size-3.5 text-primary" />
                      ) : null}
                    </button>
                  )
                })
              ) : (
                <p className="px-2.5 py-4 text-center text-xs text-muted-foreground">
                  {t('header.currentUser.noUsers')}
                </p>
              )}
            </div>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
