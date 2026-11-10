import studioI18n from '#/i18n/core'
import {
  ScopeSelector,
  ScopeSwitcher,
} from '@openviking/react/components/scope-switcher'
import '@openviking/react/components/scope-switcher/style.css'
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
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Building2Icon,
  CloudIcon,
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
  const serviceOptions = services
    .filter(
      ({ profile }) =>
        !managementMode ||
        resolveConfiguredServiceProvider(
          profile.connection.baseUrl,
          profile.connection.serviceSelection,
        ) === 'opensource',
    )
    .map(({ profile, name }) => ({
      id: profile.id,
      label: name,
      description: profile.connection.baseUrl,
    }))
  const accountOptions = (
    canListAccounts
      ? (accounts.data ?? []).map((account) => account.accountId)
      : savedAccounts
  ).map((id) => ({ id, label: id, icon: <Building2Icon className="size-4" /> }))
  const userOptions = savedUsers.map((identity) => ({
    id: identity.userId,
    label: identity.userId,
    icon: <UserRoundIcon className="size-4" />,
    disabled: serverMode === 'dev',
  }))
  if (
    assertedIdentity &&
    connection.userId &&
    !userOptions.some((option) => option.id === connection.userId)
  ) {
    userOptions.push({
      id: connection.userId,
      label: connection.userId,
      icon: <UserRoundIcon className="size-4" />,
      disabled: true,
    })
  }
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
      <ScopeSwitcher
        locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        style={
          {
            '--ov-ui-scope-switcher-panel-width': '336px',
            '--ov-ui-scope-switcher-panel-max-height': '420px',
          } as React.CSSProperties
        }
        className={cn(
          'flex h-9 min-w-0 items-center rounded-lg border border-border/60 bg-muted/30 p-0.5',
          managementMode &&
            'studio-management-scope rounded-full bg-transparent',
        )}
      >
        <ScopeSelector
          locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
          kind="selectable"
          identity={{ key: 'service', ariaLabel: t('connections.switch') }}
          trigger={{
            label: activeService?.name || host(connection.baseUrl),
            icon:
              provider === 'volcengine' ? (
                <CloudIcon className="size-3.5" />
              ) : (
                <ServerIcon className="size-3.5" />
              ),
          }}
          data={{
            options: serviceOptions,
            visibleOptions: serviceOptions.filter((option) =>
              matches(`${option.label} ${option.description}`),
            ),
            selectedId:
              services.find((service) => service.selected)?.profile.id ?? '',
          }}
          state={{
            visible: open === 'connection',
            searchKeyword: search,
          }}
          actions={{
            onVisibleChange: changeOpen('connection'),
            onSearchKeywordChange: setSearch,
            onSelect: (option) => {
              if (
                !services.find((service) => service.profile.id === option.id)
                  ?.selected
              )
                activateProfile(option.id)
            },
          }}
          labels={{
            searchPlaceholder: t('scope.searchConnections'),
            empty: t('scope.noConnections'),
          }}
          slots={{
            renderPanel: ({ search: searchControl, list }) => (
              <>
                <p className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                  {t('connections.title')}
                </p>
                {searchControl}
                {list}
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
              </>
            ),
          }}
        />
        {showAccountScope ? (
          <>
            {managementMode && !canListAccounts ? (
              <ScopeSelector
                locale={
                  studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'
                }
                kind="fixed"
                identity={{
                  key: 'account',
                  ariaLabel: t('scope.managementAccount'),
                }}
                trigger={{
                  label: connection.accountId || t('unset'),
                  icon: <Building2Icon className="size-3.5" />,
                }}
              />
            ) : (
              <ScopeSelector
                locale={
                  studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'
                }
                kind="selectable"
                identity={{
                  key: 'account',
                  ariaLabel: t('scope.switchAccount'),
                }}
                trigger={{
                  label: connection.accountId || t('unset'),
                  icon: <Building2Icon className="size-3.5" />,
                }}
                data={{
                  options: accountOptions,
                  visibleOptions: accountOptions.filter((option) =>
                    matches(option.id),
                  ),
                  selectedId: connection.accountId,
                }}
                state={{
                  visible: open === 'account',
                  searchKeyword: search,
                  busy: pending,
                  loading: canListAccounts && accounts.isPending,
                  error: canListAccounts && accounts.isError,
                }}
                actions={{
                  onVisibleChange: changeOpen('account'),
                  onSearchKeywordChange: setSearch,
                  onSelect: (option) => {
                    if (canListAccounts) void chooseAccount(option.id)
                    else if (option.id !== connection.accountId) {
                      const identity = identities.find(
                        (entry) => entry.accountId === option.id,
                      )
                      if (identity) void chooseSavedIdentity(identity)
                    }
                  },
                }}
                labels={{
                  searchPlaceholder: t('searchPlaceholder'),
                  loading: t('loading'),
                  empty: t('empty'),
                }}
                slots={{
                  errorFallback: loadError(() => accounts.refetch()),
                  renderPanel: ({ search: searchControl, list }) => (
                    <>
                      <p className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                        {t(
                          canListAccounts
                            ? 'serverAccountsTitle'
                            : 'scope.savedAccounts',
                        )}
                      </p>
                      {searchControl}
                      {list}
                      {!canListAccounts
                        ? notice(t('scope.savedAccountsHint'))
                        : null}
                      {canListAccounts && managementMode && !isGatewayHost ? (
                        <ScopeCreateAccount
                          connection={adminConnection}
                          onCreated={() => setOpen(null)}
                        />
                      ) : null}
                    </>
                  ),
                }}
              />
            )}
            {!managementMode ? (
              <>
                <ScopeSelector
                  locale={
                    studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'
                  }
                  kind="selectable"
                  identity={{ key: 'user', ariaLabel: t('scope.switchUser') }}
                  trigger={{
                    label:
                      hasUser && connection.userId
                        ? connection.userId
                        : t('appShell:modes.selectUser'),
                    icon: (
                      <UserRoundIcon className="size-3.5 text-muted-foreground" />
                    ),
                  }}
                  data={{
                    options: userOptions,
                    visibleOptions: userOptions.filter((option) =>
                      matches(option.id),
                    ),
                    selectedId: hasUser ? connection.userId : '',
                  }}
                  state={{
                    visible: open === 'user',
                    alwaysShowSearch: trusted,
                    searchKeyword: search,
                    busy: pending,
                    loading: trusted && trustedUsers.isPending,
                    error: trusted && trustedUsers.isError,
                  }}
                  actions={{
                    onVisibleChange: changeOpen('user'),
                    onSearchKeywordChange: setSearch,
                    onSelect: (option) => {
                      const identity = savedUsers.find(
                        (item) => item.userId === option.id,
                      )
                      if (identity) void chooseSavedIdentity(identity)
                    },
                  }}
                  labels={{
                    searchPlaceholder: t('scope.searchUsers'),
                    loading: t('loading'),
                    empty: t('scope.noSavedUsers'),
                  }}
                  slots={{
                    errorFallback: loadError(() => trustedUsers.refetch()),
                    renderPanel: ({ search: searchControl, list }) => (
                      <>
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
                        {searchControl}
                        {list}
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
                            className={cn(
                              ROW,
                              'mt-1 rounded-none border-t pt-3',
                            )}
                            onClick={() => setOpen(null)}
                          >
                            <PlusIcon className="size-4" />
                            {t('scope.addUserKey')}
                          </Link>
                        ) : null}
                      </>
                    ),
                  }}
                />
              </>
            ) : null}
          </>
        ) : null}
      </ScopeSwitcher>
      {managementMode && showAccountScope ? (
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
