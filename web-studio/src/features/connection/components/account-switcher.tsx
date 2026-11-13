import { showErrorToast } from '#/shared/lib/error-toast'
import { newConnectionPath } from '#/features/connection/lib/studio-mode'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  ArrowLeftIcon,
  Building2Icon,
  CheckIcon,
  ChevronDownIcon,
  CloudIcon,
  LoaderCircleIcon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
  UserRoundIcon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { DEFAULT_USER_ID } from '#/features/connection/lib/admin-options'
import { formatHostedIdentity } from '#/features/connection/lib/identity-label'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import { connectionProfileFingerprint } from '#/features/connection/lib/profiles'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import type {
  AdminConnection,
  AdminUser,
  CreateAccountInput,
} from '#/shared/api/admin'
import {
  createAdminAccount,
  fetchAdminAccounts,
  fetchAdminUsers,
} from '#/shared/api/admin'
import { ovClient } from '#/shared/api/ov-client/index'
import { OpenVikingMark } from '#/shared/components/openviking-mark'
import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Input } from '#/shared/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'

export function selectAccountUser(
  users: readonly AdminUser[],
  _currentUserId: string,
  requireApiKey: boolean,
): AdminUser | undefined {
  const candidates = requireApiKey
    ? users.filter((user) => Boolean(user.apiKey))
    : [...users]
  return candidates[0]
}

function currentRuntimeIdentity(): string {
  const options = ovClient.getOptions()
  const connection = ovClient.getConnection()
  return JSON.stringify([
    options.baseUrl,
    options.apiKeyAuth,
    connection.accountId,
    connection.userId,
    connection.apiKey,
    connection.adminApiKey,
    connection.identityHeaders,
  ])
}

function serviceKey(connection: ConnectionProfile['connection']): string {
  return JSON.stringify([
    resolveConfiguredServiceProvider(
      connection.baseUrl,
      connection.serviceSelection,
    ),
    connection.baseUrl.trim().replace(/\/+$/, ''),
  ])
}

function serviceHost(baseUrl: string): string {
  try {
    return new URL(baseUrl).host
  } catch {
    return baseUrl
  }
}

export function AccountSwitcher({
  allowManagement = true,
  settingsPath = '/settings',
  variant = 'sidebar',
}: {
  variant?: 'sidebar' | 'header'
  allowManagement?: boolean
  settingsPath?: '/settings' | '/admin/settings'
}) {
  const { t } = useTranslation('accountSwitcher')
  const queryClient = useQueryClient()
  const {
    connection,
    profiles,
    activeProfileId,
    identityScopeKey,
    activateProfile,
    connectionRole,
    isConnectionRoleLoading,
    isGatewayHost,
    setGeneratedCredential,
    serverMode,
    switchIdentity,
    switchManagementAccount,
  } = useAppConnection()
  const [open, setOpen] = React.useState(false)
  const [createOpen, setCreateOpen] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const [serviceSearch, setServiceSearch] = React.useState('')
  const [identitySearch, setIdentitySearch] = React.useState('')
  const [selectedService, setSelectedService] = React.useState<string | null>(
    null,
  )
  const [view, setView] = React.useState<'identities' | 'accounts'>('accounts')
  const [mobileDetails, setMobileDetails] = React.useState(false)
  const currentService = serviceKey(connection)
  const selectedIsActive =
    !selectedService || selectedService === currentService
  const [switchingAccountId, setSwitchingAccountId] = React.useState('')
  const [manualSwitch, setManualSwitch] = React.useState<{
    accountId: string
    apiKey: string
    userId: string
  } | null>(null)
  const latestIdentityScopeKey = React.useRef(identityScopeKey)
  latestIdentityScopeKey.current = identityScopeKey
  const previousIdentityScopeKey = React.useRef(identityScopeKey)
  React.useLayoutEffect(() => {
    if (previousIdentityScopeKey.current !== identityScopeKey) {
      setManualSwitch(null)
      setOpen(false)
    }
    previousIdentityScopeKey.current = identityScopeKey
  }, [identityScopeKey])
  const [createDraft, setCreateDraft] = React.useState<CreateAccountInput>({
    accountId: '',
    adminUserId: DEFAULT_USER_ID,
  })

  const { provider } = useStudioService()
  const capabilities = resolveStudioManagementCapabilities({
    serviceProvider: provider,
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })

  const canManageAccounts =
    allowManagement &&
    (isGatewayHost
      ? connectionRole === 'root' && !isConnectionRoleLoading
      : capabilities.canManageAccounts)

  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: connection.accountId,
      apiKey:
        connection.adminApiKey || (isGatewayHost ? connection.apiKey : ''),
      baseUrl: connection.baseUrl,
      userId: connection.userId,
    }),
    [
      connection.accountId,
      connection.adminApiKey,
      connection.apiKey,
      isGatewayHost,
      connection.baseUrl,
      connection.userId,
    ],
  )

  const accountsQuery = useQuery({
    enabled:
      canManageAccounts && open && selectedIsActive && view === 'accounts',
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

    const sourceIdentityScopeKey = identityScopeKey
    const sourceRuntimeIdentity = currentRuntimeIdentity()
    const assertCurrentIdentity = () => {
      if (
        latestIdentityScopeKey.current !== sourceIdentityScopeKey ||
        currentRuntimeIdentity() !== sourceRuntimeIdentity
      ) {
        throw new Error(t('studio:profiles.connectionChanged'))
      }
    }

    setSwitchingAccountId(accountId)
    try {
      if (isGatewayHost) {
        await switchManagementAccount(accountId)
        setOpen(false)
        toast.success(t('toast.switched', { account: accountId }))
        return
      }
      const users = await fetchAdminUsers(adminConnection, accountId)
      assertCurrentIdentity()
      const firstUser = selectAccountUser(users, connection.userId, false)
      if (!firstUser) {
        throw new Error(t('errors.noUsers'))
      }

      const candidates =
        serverMode === 'api_key'
          ? users.filter((user) => Boolean(user.apiKey))
          : [firstUser]
      let switchError: unknown
      for (const user of candidates) {
        assertCurrentIdentity()
        try {
          await switchIdentity({
            accountId,
            allowLegacyIdentityFallback: true,
            apiKey: user.apiKey || '',
            userId: user.userId,
          })
          setOpen(false)
          toast.success(
            t('toast.switched', {
              account: accountId,
            }),
          )
          return
        } catch (error) {
          // A profile change invalidates this entire selection, including
          // retries with other users and the manual-key fallback.
          if (
            error &&
            typeof error === 'object' &&
            'code' in error &&
            error.code === 'connectionChanged'
          ) {
            throw error
          }
          switchError = error
        }
      }

      assertCurrentIdentity()
      if (serverMode === 'api_key') {
        setOpen(false)
        setManualSwitch({
          accountId,
          apiKey: '',
          userId: firstUser.userId,
        })
        return
      }
      throw switchError
    } catch (error) {
      showErrorToast(error)
    } finally {
      setSwitchingAccountId('')
    }
  }

  const createAccount = useMutation({
    mutationFn: (input: CreateAccountInput) =>
      createAdminAccount(adminConnection, input),
    onError: (error) => showErrorToast(error),
    onSuccess: async (result, input) => {
      const accountId = result.accountId || input.accountId
      const userId = result.userId || input.adminUserId
      if (result.apiKey) {
        setGeneratedCredential({
          accountId,
          apiKey: result.apiKey,
          userId,
        })
      }
      setCreateOpen(false)
      setOpen(false)
      setCreateDraft({ accountId: '', adminUserId: DEFAULT_USER_ID })
      void queryClient.invalidateQueries({ queryKey: ['account-switcher'] })

      if (!result.apiKey && serverMode === 'api_key') {
        await switchManagementAccount(accountId)
        toast.warning(t('errors.noCreatedKey'))
        return
      }

      try {
        await switchIdentity({
          accountId,
          allowLegacyIdentityFallback: true,
          apiKey: result.apiKey,
          userId,
        })
        toast.success(t('toast.created', { account: input.accountId }))
      } catch (error) {
        await switchManagementAccount(accountId)
        toast.error(
          t('toast.createdSwitchFailed', {
            account: input.accountId,
            error: error instanceof Error ? error.message : String(error),
          }),
        )
      }
    },
  })

  const seenProfiles = new Set<string>()
  const savedProfiles = [...profiles]
    .sort((a, b) =>
      a.id === activeProfileId ? -1 : b.id === activeProfileId ? 1 : 0,
    )
    .filter((profile) => {
      const key = connectionProfileFingerprint(profile.connection)
      if (seenProfiles.has(key)) return false
      seenProfiles.add(key)
      return true
    })
  const groups = new Map<
    string,
    {
      connection: ConnectionProfile['connection']
      profiles: ConnectionProfile[]
    }
  >()
  groups.set(currentService, { connection, profiles: [] })
  for (const profile of savedProfiles) {
    const key = serviceKey(profile.connection)
    if (!groups.has(key))
      groups.set(key, { connection: profile.connection, profiles: [] })
    groups.get(key)!.profiles.push(profile)
  }
  const selectedKey =
    selectedService && groups.has(selectedService)
      ? selectedService
      : currentService
  const selected = groups.get(selectedKey)!
  const titleFor = (c: ConnectionProfile['connection']) =>
    c.serviceSelection === 'volcengine'
      ? t('provider.hosted')
      : serviceHost(c.baseUrl)
  const identityFor = (c: ConnectionProfile['connection']) =>
    c.serviceSelection === 'volcengine'
      ? formatHostedIdentity(c.accountId, c.userId) || t('unset')
      : `${c.accountId}/${c.userId}`
  const visibleGroups = [...groups].filter(([, group]) =>
    `${titleFor(group.connection)} ${group.connection.baseUrl}`
      .toLowerCase()
      .includes(serviceSearch.trim().toLowerCase()),
  )
  const visibleProfiles = selected.profiles.filter((profile) =>
    `${identityFor(profile.connection)} ${profile.name}`
      .toLowerCase()
      .includes(identitySearch.trim().toLowerCase()),
  )
  const showAccountsTab =
    allowManagement &&
    resolveConfiguredServiceProvider(
      selected.connection.baseUrl,
      selected.connection.serviceSelection,
    ) === 'opensource'
  const accountsVisible = showAccountsTab && view === 'accounts'
  const accountAccessReady = canManageAccounts && selectedKey === currentService
  function changeOpen(next: boolean) {
    setOpen(next)
    if (next) {
      setSelectedService(currentService)
      setServiceSearch('')
      setIdentitySearch('')
      setSearch('')
      setMobileDetails(false)
      setView('identities')
    }
  }

  return (
    <>
      <Popover open={open} onOpenChange={changeOpen}>
        <PopoverTrigger
          aria-label={t('connections.switch')}
          className={cn(
            'flex min-w-0 items-center text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
            variant === 'header'
              ? 'h-9 max-w-full gap-2 rounded-lg border border-border/60 bg-muted/30 px-2.5 text-xs hover:bg-accent sm:text-sm'
              : 'min-h-12 w-full gap-2 rounded-lg px-1 hover:bg-sidebar-accent group-data-[collapsible=icon]:size-9 group-data-[collapsible=icon]:justify-center',
          )}
        >
          {variant === 'header' ? (
            <>
              <span
                className="hidden min-w-0 items-center gap-2 sm:flex"
                title={connection.baseUrl}
              >
                <CloudIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="max-w-44 truncate">
                  {titleFor(connection)}
                </span>
                <span className="text-border" aria-hidden="true">
                  /
                </span>
              </span>
              <span className="flex min-w-0 items-center gap-1.5">
                <Building2Icon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="max-w-24 truncate">
                  {connection.accountId}
                </span>
              </span>
              <span className="text-border" aria-hidden="true">
                /
              </span>
              <span className="flex min-w-0 items-center gap-1.5">
                <UserRoundIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="max-w-24 truncate">
                  {connection.apiKey || serverMode === 'trusted'
                    ? connection.userId
                    : t('appShell:modes.selectUser')}
                </span>
              </span>
            </>
          ) : (
            <>
              <span className="flex size-8 shrink-0 items-center justify-center text-sidebar-foreground">
                <OpenVikingMark className="size-7" />
              </span>
              <span className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
                <span
                  className="block truncate text-sm font-semibold"
                  title={connection.baseUrl}
                >
                  {titleFor(connection)}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  [{identityFor(connection)}]
                </span>
              </span>
            </>
          )}
          <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground group-data-[collapsible=icon]:hidden" />
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={6}
          className="w-[min(40rem,calc(100vw-2rem))] gap-0 overflow-hidden p-0"
        >
          <div className="grid min-h-80 sm:grid-cols-[12rem_minmax(0,1fr)]">
            <div
              className={cn(
                'flex min-w-0 flex-col sm:border-r',
                mobileDetails && 'hidden sm:flex',
              )}
            >
              <div className="p-3">
                <Input
                  value={serviceSearch}
                  onChange={(event) => setServiceSearch(event.target.value)}
                  aria-label={t('servicesSearch')}
                  placeholder={t('servicesSearch')}
                  className="h-9"
                />
              </div>
              <div className="max-h-72 flex-1 overflow-y-auto px-1.5 pb-2">
                {visibleGroups.map(([key, group]) => {
                  const Icon =
                    group.connection.serviceSelection === 'volcengine'
                      ? CloudIcon
                      : ServerIcon
                  return (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={key === selectedKey}
                      title={group.connection.baseUrl}
                      onClick={() => {
                        setSelectedService(key)
                        setView('identities')
                        setIdentitySearch('')
                        setMobileDetails(true)
                      }}
                      className={cn(
                        'flex min-h-11 w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        key === selectedKey && 'bg-accent',
                      )}
                    >
                      <Icon className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 truncate font-medium">
                        {titleFor(group.connection)}
                      </span>
                    </button>
                  )
                })}
                {visibleGroups.length === 0 && (
                  <p className="px-3 py-5 text-xs text-muted-foreground">
                    {t('noConnections')}
                  </p>
                )}
              </div>
              <Link
                to={newConnectionPath(settingsPath)}
                onClick={() => setOpen(false)}
                className="mt-auto flex h-11 shrink-0 items-center gap-2 border-t px-3 text-xs hover:bg-accent"
              >
                <PlusIcon className="size-3.5" />
                {t('connections.add')}
              </Link>
            </div>
            <div
              className={cn(
                'flex min-w-0 flex-col',
                !mobileDetails && 'hidden sm:flex',
              )}
            >
              <div className="flex min-h-14 items-center gap-2 px-4 py-3">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 sm:hidden"
                  aria-label={t('backToServices')}
                  onClick={() => setMobileDetails(false)}
                >
                  <ArrowLeftIcon className="size-4" />
                </Button>
                <span
                  className="min-w-0 truncate text-sm font-semibold"
                  title={selected.connection.baseUrl}
                >
                  {titleFor(selected.connection)}
                </span>
                {selectedKey === currentService && (
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {t('connected')}
                  </span>
                )}
              </div>
              <div className="flex gap-4 border-b px-4">
                <button
                  type="button"
                  aria-pressed={!accountsVisible}
                  onClick={() => setView('identities')}
                  className={cn(
                    'min-h-9 border-b-2 text-xs',
                    !accountsVisible
                      ? 'border-foreground font-medium'
                      : 'border-transparent text-muted-foreground',
                  )}
                >
                  {t('identities')}
                </button>
                {showAccountsTab && (
                  <button
                    type="button"
                    aria-pressed={accountsVisible}
                    onClick={() => setView('accounts')}
                    className={cn(
                      'min-h-9 border-b-2 text-xs',
                      accountsVisible
                        ? 'border-foreground font-medium'
                        : 'border-transparent text-muted-foreground',
                    )}
                  >
                    {t('serverAccountsTitle')}
                  </button>
                )}
              </div>
              <div className="min-h-0 flex-1">
                {accountsVisible && !accountAccessReady ? (
                  <div className="space-y-3 p-4 text-xs text-muted-foreground">
                    <p>
                      {t(
                        selectedKey !== currentService
                          ? 'accountsConnectFirst'
                          : isConnectionRoleLoading
                            ? 'accountsChecking'
                            : 'accountsRootRequired',
                      )}
                    </p>
                    {selectedKey === currentService &&
                      !isConnectionRoleLoading && (
                        <Link
                          to={settingsPath}
                          onClick={() => setOpen(false)}
                          className="text-foreground underline underline-offset-4"
                        >
                          {t('manageConnections')}
                        </Link>
                      )}
                  </div>
                ) : accountsVisible ? (
                  <>
                    <div className="flex items-center gap-2 p-3">
                      <div className="relative min-w-0 flex-1">
                        <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          value={search}
                          onChange={(event) => setSearch(event.target.value)}
                          aria-label={t('searchPlaceholder')}
                          placeholder={t('searchPlaceholder')}
                          className="h-9 pl-8 text-xs"
                        />
                      </div>
                      {!isGatewayHost && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-9 shrink-0 text-xs"
                          onClick={() => {
                            setOpen(false)
                            setCreateOpen(true)
                          }}
                        >
                          <PlusIcon className="size-3.5" />
                          {t('create')}
                        </Button>
                      )}
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
                          const active =
                            account.accountId === connection.accountId
                          const switching =
                            switchingAccountId === account.accountId
                          return (
                            <button
                              key={account.accountId}
                              type="button"
                              disabled={Boolean(switchingAccountId)}
                              onClick={() =>
                                void selectAccount(account.accountId)
                              }
                              className={cn(
                                'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent disabled:cursor-wait disabled:opacity-60',
                                active && 'bg-accent/70',
                              )}
                            >
                              <span className="flex size-7 shrink-0 items-center justify-center rounded-md border bg-background">
                                <Building2Icon className="size-3.5" />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="flex min-w-0 items-center gap-1">
                                  <span className="min-w-0 truncate text-sm font-medium">
                                    {account.accountId}
                                  </span>
                                </span>
                                <span className="block text-xs text-muted-foreground">
                                  {t('memberCount', {
                                    count: account.userCount,
                                  })}
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
                  </>
                ) : (
                  <>
                    <div className="p-3">
                      <Input
                        value={identitySearch}
                        onChange={(event) =>
                          setIdentitySearch(event.target.value)
                        }
                        aria-label={t('identitiesSearch')}
                        placeholder={t('identitiesSearch')}
                        className="h-9"
                      />
                    </div>
                    <div className="max-h-60 space-y-1 overflow-y-auto px-2 pb-2">
                      {visibleProfiles.map((profile) => {
                        const identity = identityFor(profile.connection)
                        const duplicateIdentity =
                          selected.profiles.filter(
                            (candidate) =>
                              identityFor(candidate.connection) === identity,
                          ).length > 1
                        return (
                          <button
                            key={profile.id}
                            type="button"
                            aria-current={
                              profile.id === activeProfileId
                                ? 'true'
                                : undefined
                            }
                            title={profile.name}
                            onClick={() => {
                              setOpen(false)
                              activateProfile(profile.id)
                            }}
                            className={cn(
                              'flex min-h-11 w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                              profile.id === activeProfileId && 'bg-accent',
                            )}
                          >
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium">
                                {profile.name}
                              </span>
                              <span className="block truncate text-[11px] text-muted-foreground">
                                [{identity}]
                              </span>
                            </span>
                            {duplicateIdentity && (
                              <span
                                className="shrink-0 font-mono text-[10px] text-muted-foreground"
                                title={profile.id}
                              >
                                #{profile.id.slice(0, 8)}
                              </span>
                            )}
                            {profile.id === activeProfileId && (
                              <CheckIcon className="size-3.5 shrink-0" />
                            )}
                          </button>
                        )
                      })}
                      {visibleProfiles.length === 0 && (
                        <p className="px-3 py-5 text-xs text-muted-foreground">
                          {t('noIdentities')}
                        </p>
                      )}
                    </div>
                    <p className="px-4 pb-3 text-[11px] text-muted-foreground">
                      {t('connections.savedOnly')}
                    </p>
                  </>
                )}
              </div>
              <Link
                to={
                  accountsVisible && accountAccessReady
                    ? '/admin/users'
                    : settingsPath
                }
                onClick={() => setOpen(false)}
                className="mt-auto flex h-11 shrink-0 items-center border-t px-4 text-xs hover:bg-accent"
              >
                {t(
                  accountsVisible && accountAccessReady
                    ? 'accountSettings'
                    : 'manageConnections',
                )}
              </Link>
            </div>
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
                userId: target.userId,
              })
                .then(() => {
                  toast.success(
                    t('toast.switched', { account: target.accountId }),
                  )
                  setManualSwitch(null)
                })
                .catch((error: unknown) => {
                  showErrorToast(error)
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
                onClick={() => {
                  const targetAccountId = manualSwitch?.accountId
                  if (!targetAccountId) {
                    return
                  }
                  setSwitchingAccountId(targetAccountId)
                  void switchManagementAccount(targetAccountId)
                    .then(() => {
                      setManualSwitch(null)
                      toast.success(
                        t('toast.managementSwitched', {
                          account: targetAccountId,
                        }),
                      )
                    })
                    .catch((error: unknown) => {
                      showErrorToast(error)
                    })
                    .finally(() => setSwitchingAccountId(''))
                }}
              >
                {t('manualSwitch.manageOnly')}
              </Button>
              <Button
                type="button"
                variant="ghost"
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
