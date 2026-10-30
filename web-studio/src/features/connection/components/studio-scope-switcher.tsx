import { ScopeCreateAccount } from '#/features/connection/components/scope-create-account'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import type { SavedDataIdentity } from '#/features/connection/lib/profiles'
import {
  groupProfilesByService,
  savedDataIdentities,
} from '#/features/connection/lib/profiles'
import { newConnectionPath } from '#/features/connection/lib/studio-mode'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { fetchAdminAccounts, fetchAdminUsers } from '#/shared/api/admin'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { LucideIcon } from 'lucide-react'
import {
  Building2Icon,
  CheckIcon,
  ChevronDownIcon,
  CloudIcon,
  LoaderCircleIcon,
  PlusIcon,
  ServerIcon,
  ShieldCheckIcon,
  UserRoundIcon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

const ROW =
  'flex min-h-10 w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50'
type Scope = 'connection' | 'account' | 'user'
function ScopeMenu({
  open,
  onOpenChange,
  label,
  value,
  icon: Icon,
  readOnly = false,
  children,
}: React.PropsWithChildren<{
  open: boolean
  onOpenChange: (open: boolean) => void
  label: string
  value: string
  icon: LucideIcon
  readOnly?: boolean
}>) {
  if (readOnly) {
    return (
      <div
        role="group"
        aria-label={label}
        title={value}
        className="flex h-9 min-w-0 items-center gap-1 px-1.5 text-xs sm:gap-1.5 sm:px-2.5 sm:text-sm"
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="max-w-24 truncate sm:max-w-40">{value}</span>
      </div>
    )
  }
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        aria-label={label}
        title={value}
        className="flex h-9 min-w-0 items-center gap-1 rounded-md px-1.5 text-xs outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring sm:gap-1.5 sm:px-2.5 sm:text-sm"
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="max-w-24 truncate sm:max-w-40">{value}</span>
        <ChevronDownIcon className="hidden size-3 shrink-0 text-muted-foreground sm:block" />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-[min(21rem,calc(100vw-1.5rem))] gap-0 overflow-hidden rounded-xl p-1.5 shadow-lg"
      >
        {children}
      </PopoverContent>
    </Popover>
  )
}
function host(url: string) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function StudioScopeSwitcher({
  settingsPath,
  managementMode,
  showAccountScope = true,
}: {
  settingsPath: '/settings' | '/admin/settings'
  managementMode: boolean
  showAccountScope?: boolean
}) {
  const { t } = useTranslation('accountSwitcher')
  const {
    connection,
    profiles,
    activeProfileId,
    activateProfile,
    identityScopeKey,
    connectionRole,
    isConnectionRoleLoading,
    isGatewayHost,
    serverMode,
    switchIdentity,
    switchManagementAccount,
  } = useAppConnection()
  const { provider } = useStudioService()
  const trusted = serverMode === 'trusted' && Boolean(connection.adminApiKey)
  const [open, setOpen] = React.useState<Scope | null>(null)
  const [search, setSearch] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const services = groupProfilesByService(profiles).map((group) => {
    const active = group.find((profile) => profile.id === activeProfileId)
    const profile =
      active ??
      (managementMode
        ? group.find((entry) => Boolean(entry.connection.adminApiKey))
        : undefined) ??
      group[0]
    // Earlier identity switches created profiles named Account/User. Keep that
    // identity detail in the other selectors, and keep the service name stable.
    const named = group.find(
      (entry) =>
        entry.name !==
        `${entry.connection.accountId}/${entry.connection.userId}`,
    )
    return {
      profile,
      name: named?.name || host(profile.connection.baseUrl),
      selected: Boolean(active),
    }
  })
  const activeService = services.find((service) => service.selected)
  const capabilities = resolveStudioManagementCapabilities({
    serviceProvider: provider,
    hasControlCredential: Boolean(connection.adminApiKey),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const canListAccounts =
    showAccountScope &&
    (managementMode || trusted) &&
    ((trusted && !isConnectionRoleLoading) ||
      capabilities.canManageAccounts ||
      (isGatewayHost && connectionRole === 'root' && !isConnectionRoleLoading))
  const adminConnection = {
    ...connection,
    apiKey: connection.adminApiKey || (isGatewayHost ? connection.apiKey : ''),
  }
  const accounts = useQuery({
    queryKey: ['scope-accounts', identityScopeKey],
    queryFn: () => fetchAdminAccounts(adminConnection),
    enabled: open === 'account' && canListAccounts,
    retry: false,
  })
  const identities = savedDataIdentities(
    [
      ...profiles.filter((profile) => profile.id === activeProfileId),
      ...profiles.filter((profile) => profile.id !== activeProfileId),
    ],
    connection,
  )
  const trustedUsers = useQuery({
    queryKey: ['trusted-scope-users', identityScopeKey],
    queryFn: () =>
      fetchAdminUsers(adminConnection, connection.accountId, false),
    enabled: trusted && open === 'user' && Boolean(connection.accountId),
    retry: false,
  })
  const savedUsers = trusted
    ? (trustedUsers.data ?? []).map((user) => ({
        accountId: connection.accountId,
        userId: user.userId,
        apiKey: '',
      }))
    : identities.filter(
        (entry, index) =>
          entry.accountId === connection.accountId &&
          identities.findIndex(
            (candidate) =>
              candidate.accountId === entry.accountId &&
              candidate.userId === entry.userId,
          ) === index,
      )
  const savedAccounts = [
    ...new Set([
      connection.accountId,
      ...identities.map((entry) => entry.accountId),
    ]),
  ].filter(Boolean)
  const hasUser =
    Boolean(connection.apiKey) ||
    serverMode === 'trusted' ||
    serverMode === 'dev'
  const assertedIdentity = serverMode === 'trusted' || serverMode === 'dev'
  const managementCredential = t(
    isConnectionRoleLoading
      ? 'scope.checkingManagement'
      : serverMode === 'trusted'
        ? 'scope.trustedManagement'
        : serverMode === 'dev'
          ? 'scope.devManagement'
          : connectionRole === 'root'
            ? 'scope.rootManagement'
            : connectionRole === 'admin'
              ? 'scope.accountManagement'
              : 'scope.unverifiedManagement',
  )
  const matches = (text: string) =>
    text.toLowerCase().includes(search.trim().toLowerCase())
  const changeOpen = (scope: Scope) => (next: boolean) => {
    setOpen(next ? scope : null)
    setSearch('')
  }
  const errorMessage = (error: unknown) =>
    error instanceof Error ? error.message : String(error)
  async function chooseAccount(accountId: string) {
    if (pending || accountId === connection.accountId) {
      setOpen(null)
      return
    }
    if (trusted && !managementMode) {
      await chooseSavedIdentity({ accountId, userId: 'default', apiKey: '' })
      return
    }
    setPending(true)
    try {
      await switchManagementAccount(accountId, { preserveProfile: true })
      setOpen(null)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setPending(false)
    }
  }
  async function chooseSavedIdentity(
    identity: Pick<SavedDataIdentity, 'accountId' | 'userId' | 'apiKey'>,
  ) {
    if (pending) return
    if (
      identity.accountId === connection.accountId &&
      identity.userId === connection.userId &&
      identity.apiKey === connection.apiKey
    ) {
      setOpen(null)
      return
    }
    setPending(true)
    try {
      await switchIdentity({
        accountId: identity.accountId,
        userId: identity.userId,
        apiKey: identity.apiKey,
        allowLegacyIdentityFallback: true,
        preserveProfile: true,
      })
      setOpen(null)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setPending(false)
    }
  }
  const searchField = (placeholder: string) => (
    <div className="p-1.5">
      <Input
        aria-label={placeholder}
        placeholder={placeholder}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        className="h-9"
      />
    </div>
  )
  const notice = (text: string) => (
    <p className="px-3 py-3 text-xs leading-5 text-muted-foreground">{text}</p>
  )
  const loadError = (retry: () => unknown) => (
    <div className="grid gap-2 px-3 py-3">
      <p role="alert" className="text-xs text-destructive">
        {t('scope.loadFailed')}
      </p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          void retry()
        }}
      >
        {t('scope.retry')}
      </Button>
    </div>
  )

  return (
    <>
      <div className="flex h-9 min-w-0 items-center rounded-lg border border-border/60 bg-muted/30 p-0.5">
        <ScopeMenu
          open={open === 'connection'}
          onOpenChange={changeOpen('connection')}
          label={t('connections.switch')}
          value={activeService?.name || host(connection.baseUrl)}
          icon={provider === 'volcengine' ? CloudIcon : ServerIcon}
        >
          <p className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
            {t('connections.title')}
          </p>
          {searchField(t('scope.searchConnections'))}
          <div className="max-h-64 overflow-y-auto">
            {services
              .filter(({ name, profile }) =>
                matches(`${name} ${profile.connection.baseUrl}`),
              )
              .map(({ profile, name, selected }) => (
                <button
                  key={profile.id}
                  type="button"
                  className={cn(ROW, selected && 'bg-accent/60')}
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => {
                    setOpen(null)
                    if (!selected) activateProfile(profile.id)
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{name}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {profile.connection.baseUrl}
                    </span>
                  </span>
                  {selected ? (
                    <CheckIcon className="size-3.5 shrink-0" />
                  ) : null}
                </button>
              ))}
            {!services.some(({ name, profile }) =>
              matches(`${name} ${profile.connection.baseUrl}`),
            )
              ? notice(t('scope.noConnections'))
              : null}
          </div>
          <div className="mt-1 border-t pt-1">
            <Link
              to={newConnectionPath(settingsPath)}
              className={ROW}
              onClick={() => setOpen(null)}
            >
              <PlusIcon className="size-4" />
              {t('connections.add')}
            </Link>
            <Link
              to={settingsPath}
              className={ROW}
              onClick={() => setOpen(null)}
            >
              {t('manageConnections')}
            </Link>
          </div>
        </ScopeMenu>
        {showAccountScope ? (
          <>
            <span aria-hidden="true" className="text-border">
              /
            </span>
            <ScopeMenu
              open={open === 'account'}
              onOpenChange={changeOpen('account')}
              label={t(
                managementMode && !canListAccounts
                  ? 'scope.managementAccount'
                  : 'scope.switchAccount',
              )}
              readOnly={managementMode && !canListAccounts}
              value={connection.accountId || t('unset')}
              icon={Building2Icon}
            >
              <p className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                {t(
                  canListAccounts
                    ? 'serverAccountsTitle'
                    : managementMode
                      ? 'scope.managementAccount'
                      : 'scope.savedAccounts',
                )}
              </p>
              {canListAccounts ? (
                <>
                  {searchField(t('searchPlaceholder'))}
                  <div className="max-h-64 overflow-y-auto">
                    {accounts.isPending ? (
                      notice(t('loading'))
                    ) : accounts.isError ? (
                      loadError(() => accounts.refetch())
                    ) : (
                      <>
                        {accounts.data
                          .filter((account) => matches(account.accountId))
                          .map((account) => (
                            <button
                              key={account.accountId}
                              type="button"
                              className={cn(
                                ROW,
                                account.accountId === connection.accountId &&
                                  'bg-accent/60',
                              )}
                              disabled={pending}
                              onClick={() => {
                                void chooseAccount(account.accountId)
                              }}
                            >
                              <Building2Icon className="size-4 shrink-0 text-muted-foreground" />
                              <span className="min-w-0 flex-1 truncate">
                                {account.accountId}
                              </span>
                              {account.accountId === connection.accountId ? (
                                <CheckIcon className="size-3.5 shrink-0" />
                              ) : null}
                            </button>
                          ))}
                        {!accounts.data.some((account) =>
                          matches(account.accountId),
                        )
                          ? notice(t('empty'))
                          : null}
                      </>
                    )}
                  </div>
                </>
              ) : (
                <>
                  {searchField(t('searchPlaceholder'))}
                  <div className="max-h-64 overflow-y-auto">
                    {savedAccounts.filter(matches).map((accountId) => (
                      <button
                        key={accountId}
                        type="button"
                        className={cn(
                          ROW,
                          accountId === connection.accountId && 'bg-accent/60',
                        )}
                        disabled={pending}
                        onClick={() => {
                          if (accountId === connection.accountId) {
                            setOpen(null)
                            return
                          }
                          const identity = identities.find(
                            (entry) => entry.accountId === accountId,
                          )
                          if (identity) void chooseSavedIdentity(identity)
                        }}
                      >
                        <Building2Icon className="size-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">
                          {accountId}
                        </span>
                        {accountId === connection.accountId ? (
                          <CheckIcon className="size-3.5" />
                        ) : null}
                      </button>
                    ))}
                  </div>
                  {notice(t('scope.savedAccountsHint'))}
                </>
              )}
              {canListAccounts && managementMode && !isGatewayHost ? (
                <ScopeCreateAccount
                  connection={adminConnection}
                  onCreated={() => setOpen(null)}
                />
              ) : null}
            </ScopeMenu>
            {!managementMode ? (
              <>
                <span aria-hidden="true" className="text-border">
                  /
                </span>
                <ScopeMenu
                  open={open === 'user'}
                  onOpenChange={changeOpen('user')}
                  label={t('scope.switchUser')}
                  value={
                    hasUser && connection.userId
                      ? connection.userId
                      : t('appShell:modes.selectUser')
                  }
                  icon={UserRoundIcon}
                >
                  <p className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                    {t(
                      trusted
                        ? 'scope.trustedUsersInAccount'
                        : 'scope.usersInAccount',
                      {
                        account: connection.accountId,
                      },
                    )}
                  </p>
                  {searchField(t('scope.searchUsers'))}
                  <div className="max-h-64 overflow-y-auto">
                    {trusted && trustedUsers.isPending
                      ? notice(t('loading'))
                      : null}
                    {trusted && trustedUsers.isError
                      ? loadError(() => trustedUsers.refetch())
                      : null}
                    {savedUsers
                      .filter((identity) => matches(identity.userId))
                      .map((identity) => (
                        <button
                          key={identity.userId}
                          type="button"
                          className={cn(
                            ROW,
                            hasUser &&
                              identity.userId === connection.userId &&
                              'bg-accent/60',
                          )}
                          disabled={pending || serverMode === 'dev'}
                          onClick={() => {
                            void chooseSavedIdentity(identity)
                          }}
                        >
                          <UserRoundIcon className="size-4 shrink-0 text-muted-foreground" />
                          <span className="min-w-0 flex-1 truncate">
                            {identity.userId}
                          </span>
                          {pending ? (
                            <LoaderCircleIcon className="size-3.5 animate-spin" />
                          ) : hasUser &&
                            identity.userId === connection.userId ? (
                            <CheckIcon className="size-3.5" />
                          ) : null}
                        </button>
                      ))}
                    {assertedIdentity &&
                    connection.userId &&
                    !savedUsers.some(
                      (identity) => identity.userId === connection.userId,
                    ) ? (
                      <div className={ROW}>
                        <UserRoundIcon className="size-4 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">
                          {connection.userId}
                        </span>
                        <CheckIcon className="size-3.5" />
                      </div>
                    ) : null}
                    {!assertedIdentity &&
                    !savedUsers.some((identity) => matches(identity.userId))
                      ? notice(t('scope.noSavedUsers'))
                      : null}
                  </div>
                  {trusted &&
                  search.trim() &&
                  !savedUsers.some(
                    (identity) => identity.userId === search.trim(),
                  ) ? (
                    <Button
                      variant="ghost"
                      className="w-full justify-start"
                      disabled={pending}
                      onClick={() =>
                        void chooseSavedIdentity({
                          accountId: connection.accountId,
                          userId: search.trim(),
                          apiKey: '',
                        })
                      }
                    >
                      <UserRoundIcon className="size-4" />
                      {t('scope.useTrustedUser', { user: search.trim() })}
                    </Button>
                  ) : null}
                  {notice(
                    t(
                      assertedIdentity
                        ? 'scope.assertedIdentity'
                        : 'scope.savedUsersHint',
                    ),
                  )}
                  {!assertedIdentity ? (
                    <Link
                      to="/connections/user-key"
                      className={cn(ROW, 'border-t')}
                      onClick={() => setOpen(null)}
                    >
                      <PlusIcon className="size-4" />
                      {t('scope.addUserKey')}
                    </Link>
                  ) : null}
                </ScopeMenu>
              </>
            ) : null}
          </>
        ) : null}
      </div>
      {managementMode ? (
        <span
          role="status"
          aria-label={t('scope.managementCredential')}
          title={managementCredential}
          className="inline-flex min-w-0 items-center gap-1.5 rounded-md border border-border/60 bg-muted/40 px-2 py-1 text-xs text-muted-foreground"
        >
          <ShieldCheckIcon className="size-3.5 shrink-0" />
          <span className="hidden truncate sm:inline">
            {managementCredential}
          </span>
        </span>
      ) : null}
    </>
  )
}
