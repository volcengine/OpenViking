import {
  rememberSurface,
  restoreSurface,
} from '#/features/connection/lib/surface-context'
import {
  canOpenManagement,
  isConnectionSettingsPath,
  connectionSettingsPath,
  isSharedStudioPath,
  readRememberedStudioMode,
  rememberStudioMode,
  resolveStudioMode,
} from '#/features/connection/lib/studio-mode'
import { createConnectionHealthHeaders } from '#/features/connection/lib/auth'
import type {
  AppConnectionContextValue,
  GeneratedCredential,
} from '#/features/connection/lib/connection-runtime'
import {
  applyConnection,
  AUTH_PROMPT_SUPPRESSION_MS,
  connectionChangedError,
  createConnectionError,
  createConnectionRoleProbeKey,
  createIdentityScopeKey,
  createManagementAccountConnection,
  detectConnectionIdentity,
  isBrowser,
  isConnectionRole,
  normalizeConnectionDraft,
  persistConnection,
  readInitialConnection,
  resolveConnectionRoleProbeState,
  resolveSwitchedIdentity,
  shouldRedirectToLoginOnApiError,
  synchronizeConnectionRuntime,
  synchronizeResolvedDataIdentity,
} from '#/features/connection/lib/connection-runtime'
import type { ServerMode } from '#/features/connection/lib/health'
import {
  fetchServerHealth,
  normalizeBaseUrl,
  readServerIdentity,
  resetServerHealthCache,
  resolveServerAuthMode,
} from '#/features/connection/lib/health'
import type { ConnectionProfiles } from '#/features/connection/lib/profiles'
import {
  activateIdentityProfile,
  connectionProfileFingerprint,
  isValidConnectionUrl,
  normalizeProfileConnection,
  persistConnectionProfiles,
  readConnectionProfiles,
  updateActiveProfile,
} from '#/features/connection/lib/profiles'
import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/features/connection/lib/types'
import i18n from '#/i18n'
import { normalizeOvClientError, ovClient } from '#/shared/api/ov-client/index'
import { identifyServiceProvider } from '#/shared/lib/studio-service'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import * as React from 'react'

const AppConnectionContext =
  React.createContext<AppConnectionContextValue | null>(null)

export function useAppConnection(): AppConnectionContextValue {
  const context = React.useContext(AppConnectionContext)
  if (!context) {
    // eslint-disable-next-line studio-i18n/no-literal-message -- Developer invariant; retain the diagnostic for debugging.
    throw new Error(
      'useAppConnection must be used within AppConnectionProvider.',
    )
  }

  return context
}

export function AppConnectionProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const queryClient = useQueryClient()
  const authPromptSuppressedUntilRef = React.useRef(0)
  const connectionRevisionRef = React.useRef(0)
  const connectionGenerationRef = React.useRef(0)
  const hostDiscoveryRef = React.useRef<Promise<Record<
    string,
    unknown
  > | null> | null>(null)
  const profileEntryPending = React.useRef(false)
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const initialConnectionRef = React.useRef<ConnectionDraft | null>(null)
  const profilesRef = React.useRef<ConnectionProfiles | null>(null)
  const synchronizedRoleProbeRef = React.useRef<{
    key: string
    role: ConnectionRole
  } | null>(null)
  if (initialConnectionRef.current === null) {
    // The provider remounts after /config. Filesystem query keys are shared
    // across profiles, so discard results fetched under the previous identity
    // before any workspace route can read them.
    queryClient.clear()
    profilesRef.current = readConnectionProfiles(readInitialConnection())
    initialConnectionRef.current = normalizeConnectionDraft(
      restoreSurface(
        profilesRef.current.profiles.find(
          (p) => p.id === profilesRef.current!.activeId,
        )!,
        resolveStudioMode(pathname),
      ),
    )
    applyConnection(
      initialConnectionRef.current,
      'checking',
      resolveStudioMode(pathname),
    )
  }

  const [profileState, setProfileState] = React.useState(profilesRef.current!)
  const storeProfiles = React.useCallback((next: ConnectionProfiles) => {
    profilesRef.current = next
    persistConnectionProfiles(next)
    setProfileState(next)
  }, [])

  const [rawConnection, setConnection] = React.useState<ConnectionDraft>(
    initialConnectionRef.current,
  )
  const [connectionRole, setConnectionRole] =
    React.useState<ConnectionRole>('unknown')
  const [isConnectionRoleLoading, setConnectionRoleLoading] = React.useState(
    () =>
      Boolean(
        initialConnectionRef.current?.baseUrl &&
        (initialConnectionRef.current.adminApiKey ||
          initialConnectionRef.current.apiKey),
      ),
  )
  const [serverMode, setServerMode] = React.useState<ServerMode>('checking')
  const [serverHealth, setServerHealth] = React.useState<{
    key: string
    data: Record<string, unknown>
  } | null>(null)
  const [connectionAttempt, setConnectionAttempt] = React.useState(0)
  const reconnect = React.useCallback(() => {
    connectionRevisionRef.current += 1
    authPromptSuppressedUntilRef.current =
      Date.now() + AUTH_PROMPT_SUPPRESSION_MS
    synchronizedRoleProbeRef.current = null
    resetServerHealthCache()
    setServerHealth(null)
    setServerMode('checking')
    setConnectionRole('unknown')
    setConnectionRoleLoading(true)
    queryClient.clear()
    setConnectionAttempt((attempt) => attempt + 1)
  }, [queryClient])
  const [isGatewayHost, setIsGatewayHost] = React.useState(false)
  const [openvikingUrl, setOpenvikingUrl] = React.useState('')
  const [generatedCredential, setGeneratedCredential] =
    React.useState<GeneratedCredential | null>(null)

  const studioMode = resolveStudioMode(
    pathname,
    readRememberedStudioMode(
      profileState.activeId,
      rawConnection.adminApiKey && !rawConnection.apiKey
        ? 'management'
        : 'workbench',
    ),
  )
  const [surface, setSurface] = React.useState({
    profileId: profileState.activeId,
    mode: studioMode,
  })
  const changingSurface =
    surface.profileId !== profileState.activeId || surface.mode !== studioMode
  const connection = React.useMemo(() => {
    if (!changingSurface) return rawConnection
    const profile = profileState.profiles.find(
      (p) => p.id === profileState.activeId,
    )!
    const remembered =
      surface.profileId === profileState.activeId
        ? rememberSurface(profile, rawConnection, surface.mode)
        : { ...profile, connection: rawConnection }
    return restoreSurface(remembered, studioMode)
  }, [changingSurface, rawConnection, profileState, surface, studioMode])
  React.useLayoutEffect(() => {
    if (!changingSurface) return
    connectionGenerationRef.current += 1
    connectionRevisionRef.current += 1
    queryClient.clear()
    const next = {
      ...profilesRef.current!,
      profiles: profilesRef.current!.profiles.map((profile) => {
        if (profile.id !== profileState.activeId) return profile
        const remembered =
          surface.profileId === profileState.activeId
            ? rememberSurface(profile, rawConnection, surface.mode)
            : profile
        return { ...remembered, connection }
      }),
    }
    persistConnection(connection)
    storeProfiles(next)
    setConnection(connection)
    setSurface({ profileId: profileState.activeId, mode: studioMode })
  }, [
    changingSurface,
    connection,
    rawConnection,
    surface,
    studioMode,
    profileState.activeId,
    queryClient,
    storeProfiles,
  ])

  React.useEffect(() => {
    if (pathname !== '/' && !isSharedStudioPath(pathname)) {
      rememberStudioMode(profileState.activeId, resolveStudioMode(pathname))
    }
  }, [pathname, profileState.activeId])

  // Child effects may issue requests before provider effects run. Restrict the
  // shared client synchronously when crossing the management/workbench boundary.
  applyConnection(connection, serverMode, studioMode)

  React.useEffect(() => {
    if (
      !profileEntryPending.current ||
      serverMode === 'checking' ||
      serverMode === 'offline' ||
      isConnectionRoleLoading ||
      changingSurface
    )
      return
    // Editors finish their save before a profile change can select a surface.
    if (pathname.includes('/connections/')) return
    profileEntryPending.current = false
    const allowed = canOpenManagement({
      provider: identifyServiceProvider(connection.baseUrl),
      connection,
      role: connectionRole,
      isRoleLoading: false,
      serverMode,
    })
    const dataIdentity =
      Boolean(connection.apiKey) ||
      serverMode === 'trusted' ||
      serverMode === 'dev'
    const targetMode =
      studioMode === 'management' && !allowed
        ? 'workbench'
        : studioMode === 'workbench' && !dataIdentity && connection.adminApiKey
          ? 'management'
          : studioMode
    if (targetMode !== studioMode) {
      void navigate({
        replace: true,
        to: isConnectionSettingsPath(pathname)
          ? targetMode === 'management'
            ? '/admin/settings'
            : '/settings'
          : targetMode === 'management'
            ? '/admin'
            : identifyServiceProvider(connection.baseUrl) === 'volcengine'
              ? '/filesystem'
              : '/home',
      })
    }
  }, [
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    studioMode,
    changingSurface,
    pathname,
    navigate,
  ])

  const openConnectionSettings = React.useCallback(() => {
    const to = connectionSettingsPath(pathname, studioMode)
    if (pathname !== to) {
      void navigate({ to })
    }
  }, [navigate, pathname, studioMode])
  const onBootstrapAuthError = React.useEffectEvent(() => {
    openConnectionSettings()
  })

  React.useEffect(() => {
    persistConnection(connection)
  }, [connection])

  React.useEffect(() => {
    storeProfiles(updateActiveProfile(profilesRef.current!, connection))
  }, [connection, storeProfiles])

  React.useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    const isCancelled = () => cancelled
    setServerMode('checking')
    setServerHealth(null)
    void (async () => {
      // A standalone Studio is bound to the gateway serving /studio. Discover
      // that host without forwarding credentials saved for another server.
      if (
        isBrowser() &&
        window.location.pathname.startsWith('/studio') &&
        normalizeBaseUrl(connection.baseUrl) !== window.location.origin
      ) {
        hostDiscoveryRef.current ??= fetchServerHealth(
          window.location.origin,
        ).catch(() => null)
        const host = await hostDiscoveryRef.current
        if (isCancelled()) return
        if (host?.service === 'openviking-gateway') {
          setIsGatewayHost(true)
          setOpenvikingUrl(
            typeof host.openviking_url === 'string' ? host.openviking_url : '',
          )
          const next = synchronizeConnectionRuntime(
            {
              ...connection,
              serviceSelection: 'opensource',
              baseUrl: window.location.origin,
              apiKey: '',
              adminApiKey: '',
              accountId: 'default',
              userId: 'default',
            },
            'checking',
            studioMode,
          )
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
          return
        }
      }
      let health: Record<string, unknown>
      try {
        // The gateway answers an invalid OV key with health minus identity.
        try {
          health = await fetchServerHealth(
            connection.baseUrl,
            createConnectionHealthHeaders(
              connection,
              studioMode === 'management' ? 'control' : 'data',
            ),
            controller.signal,
          )
        } catch (error) {
          // A service may have changed from api_key to trusted while this
          // profile still holds an old User key. Discover the mode without
          // credentials; never retry that data request with Root in api_key mode.
          if (controller.signal.aborted || !connection.adminApiKey) throw error
          const discovery = await fetchServerHealth(
            connection.baseUrl,
            {},
            controller.signal,
          ).catch(() => null)
          if (!discovery || resolveServerAuthMode(discovery) !== 'trusted')
            throw error
          health = discovery
        }
        // Discovery is credential-free when only a control key is configured.
        // Only a server-confirmed trusted mode may use it for tenant assertions.
        if (
          resolveServerAuthMode(health) === 'trusted' &&
          (connection.adminApiKey || connection.apiKey)
        ) {
          health = await fetchServerHealth(
            connection.baseUrl,
            createConnectionHealthHeaders(
              connection,
              studioMode === 'management' ? 'control' : 'data',
              'trusted',
            ),
            controller.signal,
          )
        }
      } catch (error) {
        if (!isCancelled()) {
          setServerMode('offline')
          if (shouldRedirectToLoginOnApiError(normalizeOvClientError(error))) {
            onBootstrapAuthError()
          }
        }
        return
      }
      if (!isCancelled()) {
        setIsGatewayHost(health.service === 'openviking-gateway')
        setOpenvikingUrl(
          typeof health.openviking_url === 'string'
            ? health.openviking_url
            : '',
        )
        const mode = resolveServerAuthMode(health)
        const healthUsesDataKey =
          studioMode !== 'management' || !connection.adminApiKey
        const resolvedDataIdentity = healthUsesDataKey
          ? synchronizeResolvedDataIdentity(connection, {
              ...readServerIdentity(health),
              role: isConnectionRole(health.role) ? health.role : 'unknown',
            })
          : null
        const dataConnection = resolvedDataIdentity ?? connection
        const classified =
          mode === 'trusted' &&
          (connection.adminApiKey || connection.apiKey) &&
          connection.apiKey
            ? {
                ...connection,
                adminApiKey: connection.adminApiKey || connection.apiKey,
                apiKey: '',
              }
            : healthUsesDataKey &&
                mode !== 'trusted' &&
                identifyServiceProvider(connection.baseUrl) === 'opensource' &&
                connection.serviceSelection !== 'volcengine'
              ? health.role === 'root' && connection.apiKey
                ? { ...connection, adminApiKey: connection.apiKey, apiKey: '' }
                : health.role === 'admin' &&
                    !connection.adminApiKey &&
                    connection.apiKey
                  ? { ...dataConnection, adminApiKey: connection.apiKey }
                  : null
              : null
        const resolved = classified ?? resolvedDataIdentity
        const next = resolved
          ? synchronizeConnectionRuntime(resolved, mode, studioMode)
          : connection
        if (resolved) {
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
        }
        setServerHealth({
          key: studioMode + createConnectionRoleProbeKey(next, 'checking'),
          data: health,
        })
        setServerMode(mode)
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [
    connection.accountId,
    connection.adminApiKey,
    connection.apiKey,
    connection.baseUrl,
    connection.serviceSelection,
    connection.userId,
    connectionAttempt,
    studioMode,
    queryClient,
  ])

  React.useEffect(() => {
    let cancelled = false
    const isCancelled = () => cancelled
    const controller = new AbortController()
    const management = studioMode === 'management'
    const apiKey =
      management || serverMode === 'trusted'
        ? connection.adminApiKey || connection.apiKey
        : connection.apiKey
    const roleProbe = resolveConnectionRoleProbeState({
      apiKey,
      baseUrl: connection.baseUrl,
      serverMode,
    })
    const probeKey =
      studioMode + createConnectionRoleProbeKey(connection, serverMode)
    const synchronizedProbe = synchronizedRoleProbeRef.current

    if (synchronizedProbe?.key === probeKey) {
      synchronizedRoleProbeRef.current = null
      setConnectionRole(synchronizedProbe.role)
      setConnectionRoleLoading(false)
      return () => {
        cancelled = true
        controller.abort()
      }
    }

    setConnectionRole(roleProbe.role)
    setConnectionRoleLoading(roleProbe.isLoading)
    if (!roleProbe.shouldProbe) {
      return () => {
        cancelled = true
        controller.abort()
      }
    }

    void detectConnectionIdentity(
      connection,
      management ? 'control' : 'data',
      controller.signal,
      serverMode,
    )
      .catch(() => ({
        accountId: '',
        role: 'unknown' as const,
        userId: '',
      }))
      .then(async (controlIdentity) => {
        if (isCancelled()) {
          return
        }
        const { accountId, userId, role } = controlIdentity
        // An account-admin User key is scoped to its own account. Pin that
        // account as the assumed identity so admin and data calls target the
        // right tenant instead of failing with a mismatch (the server rejects
        // a foreign account with "ADMIN can only manage account: <x>"). A root
        // key is not account-scoped, so its account selection is left intact.
        if (
          management &&
          role === 'admin' &&
          accountId &&
          (connection.accountId !== accountId ||
            (serverMode === 'api_key' && !connection.apiKey && Boolean(userId)))
        ) {
          storeProfiles({
            ...profilesRef.current!,
            profiles: profilesRef.current!.profiles.map((profile) =>
              profile.id === profilesRef.current!.activeId &&
              !profile.workbenchIdentity &&
              connection.apiKey
                ? rememberSurface(profile, connection, 'workbench')
                : profile,
            ),
          })
          const next = synchronizeConnectionRuntime(
            {
              ...connection,
              accountId,
              ...(serverMode === 'api_key' && userId
                ? { apiKey: connection.adminApiKey, userId }
                : {}),
            },
            serverMode,
            studioMode,
          )
          synchronizedRoleProbeRef.current = {
            key: studioMode + createConnectionRoleProbeKey(next, serverMode),
            role,
          }
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
          return
        }
        setConnectionRole(role)
        setConnectionRoleLoading(false)
      })
      .catch(() => {
        if (!cancelled) {
          setConnectionRole('unknown')
          setConnectionRoleLoading(false)
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [
    connection.accountId,
    connection.adminApiKey,
    connection.apiKey,
    connection.baseUrl,
    connection.serviceSelection,
    connection.userId,
    queryClient,
    serverMode,
    studioMode,
  ])

  React.useEffect(() => {
    const interceptorId = ovClient.instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (
          shouldRedirectToLoginOnApiError(error) &&
          Date.now() >= authPromptSuppressedUntilRef.current
        ) {
          openConnectionSettings()
        }
        return Promise.reject(error)
      },
    )

    return () => {
      ovClient.instance.interceptors.response.eject(interceptorId)
    }
  }, [openConnectionSettings])

  const value = React.useMemo<AppConnectionContextValue>(() => {
    const sourceGeneration = connectionGenerationRef.current
    const sourceProfileId = profileState.activeId
    const assertSourceConnection = () => {
      if (
        sourceGeneration !== connectionGenerationRef.current ||
        sourceProfileId !== profilesRef.current?.activeId
      ) {
        throw connectionChangedError()
      }
    }

    const commitConnection = (
      next: ConnectionDraft,
      preserveIdentity = false,
      rememberIdentity = false,
      dataSelection = false,
    ) => {
      connectionGenerationRef.current += 1
      connectionRevisionRef.current += 1
      authPromptSuppressedUntilRef.current =
        Date.now() + AUTH_PROMPT_SUPPRESSION_MS
      const normalized = synchronizeConnectionRuntime(
        next,
        serverMode,
        studioMode,
      )
      if (preserveIdentity) {
        storeProfiles(activateIdentityProfile(profilesRef.current!, normalized))
      } else if (rememberIdentity) {
        storeProfiles(
          updateActiveProfile(profilesRef.current!, normalized, true),
        )
      }
      storeProfiles({
        ...profilesRef.current!,
        profiles: profilesRef.current!.profiles.map((profile) =>
          profile.id === profilesRef.current!.activeId
            ? rememberSurface(
                profile,
                normalized,
                dataSelection ? 'workbench' : studioMode,
              )
            : profile,
        ),
      })
      queryClient.clear()
      setConnection(normalized)
    }

    const activateConnection = (next: ConnectionDraft) => {
      profileEntryPending.current = true
      connectionGenerationRef.current += 1
      // Apply the new credentials before any new route effects can issue requests.
      const normalized = synchronizeConnectionRuntime(
        next,
        'checking',
        studioMode,
      )
      reconnect()
      setGeneratedCredential(null)
      setConnection(normalized)
    }

    return {
      profiles: profileState.profiles,
      activeProfileId: profileState.activeId,
      saveProfile: (profile) => {
        if (!isValidConnectionUrl(profile.connection.baseUrl))
          throw new Error(i18n.t('studio:profiles.invalidUrl'))
        const previous = profilesRef.current!.profiles.find(
          (entry) => entry.id === profile.id,
        )
        const keepSurfaces =
          previous &&
          connectionProfileFingerprint(previous.connection) ===
            connectionProfileFingerprint(profile.connection)
        const normalized = {
          ...profile,
          ...(!keepSurfaces
            ? { workbenchIdentity: undefined, managementAccountId: undefined }
            : {}),
          name: profile.name.trim(),
          connection: normalizeConnectionDraft(
            normalizeProfileConnection(profile.connection),
          ),
        }
        const current = profilesRef.current!
        const exists = current.profiles.some((p) => p.id === profile.id)
        storeProfiles({
          ...current,
          profiles: exists
            ? current.profiles.map((p) =>
                p.id === profile.id ? normalized : p,
              )
            : [...current.profiles, normalized],
        })
        if (profile.id === current.activeId)
          activateConnection(normalized.connection)
      },
      activateProfile: (id) => {
        const current = profilesRef.current!
        const profile = current.profiles.find((p) => p.id === id)
        if (!profile || id === current.activeId) return
        storeProfiles({ ...current, activeId: id })
        activateConnection(profile.connection)
      },
      deleteProfile: (id) => {
        const current = profilesRef.current!
        if (id === current.activeId) return
        storeProfiles({
          ...current,
          profiles: current.profiles.filter((p) => p.id !== id),
        })
      },
      clearGeneratedCredential: () => setGeneratedCredential(null),
      connection,
      connectionRole,
      generatedCredential,
      identityScopeKey:
        profileState.activeId +
        '\u0000' +
        createIdentityScopeKey(connection, serverMode),
      isConnectionRoleLoading,
      isGatewayHost,
      openvikingUrl,
      openConnectionSettings,
      reconnect,
      serverHealth:
        serverHealth?.key ===
        studioMode + createConnectionRoleProbeKey(connection, 'checking')
          ? serverHealth.data
          : null,
      saveConnection: (next) => {
        if (!isValidConnectionUrl(next.baseUrl))
          throw new Error(i18n.t('studio:profiles.invalidUrl'))
        commitConnection(normalizeProfileConnection(next))
      },
      setGeneratedCredential,
      serverMode,
      studioMode,
      switchIdentity: async ({
        accountId,
        allowLegacyIdentityFallback,
        preserveProfile = true,
        rememberWorkbench = true,
        signal,
        apiKey,
        userId,
      }) => {
        assertSourceConnection()
        if (serverMode === 'dev' || serverMode === 'checking') {
          throw createConnectionError('identitySwitchUnsupported')
        }

        const revision = ++connectionRevisionRef.current
        const assertCurrentConnection = () => {
          signal?.throwIfAborted()
          assertSourceConnection()
          if (revision !== connectionRevisionRef.current)
            throw connectionChangedError()
        }

        const requested = normalizeConnectionDraft({
          ...connection,
          accountId,
          apiKey: serverMode === 'trusted' ? '' : apiKey,
          userId,
        })
        const identity = await detectConnectionIdentity(
          requested,
          'data',
          signal,
          serverMode,
        )
        assertCurrentConnection()
        const resolvedIdentity = resolveSwitchedIdentity(
          requested,
          identity,
          allowLegacyIdentityFallback,
        )
        if (!resolvedIdentity) {
          throw createConnectionError('credentialMismatch')
        }

        if (pathname === '/directory' || pathname === '/memories') {
          await navigate({ replace: true, to: pathname })
        }
        if (pathname === '/filesystem') {
          await navigate({
            replace: true,
            search: { upload: false },
            to: '/filesystem',
          })
        }
        assertCurrentConnection()
        commitConnection(
          {
            ...requested,
            ...resolvedIdentity,
          },
          !preserveProfile,
          preserveProfile,
          rememberWorkbench,
        )
      },
      switchManagementAccount: async (accountId, _options) => {
        assertSourceConnection()
        if (connectionRole !== 'root') {
          throw createConnectionError('rootRequired')
        }
        storeProfiles({
          ...profilesRef.current!,
          profiles: profilesRef.current!.profiles.map((profile) =>
            profile.id === profilesRef.current!.activeId
              ? rememberSurface(profile, connection, studioMode)
              : profile,
          ),
        })
        commitConnection(
          serverMode === 'trusted'
            ? { ...connection, accountId: accountId.trim(), userId: 'default' }
            : createManagementAccountConnection(
                connection,
                accountId,
                isGatewayHost,
              ),
          false,
          true,
        )
      },
    }
  }, [
    connection,
    profileState,
    storeProfiles,
    connectionRole,
    generatedCredential,
    isConnectionRoleLoading,
    isGatewayHost,
    openvikingUrl,
    navigate,
    openConnectionSettings,
    pathname,
    queryClient,
    reconnect,
    serverHealth,
    serverMode,
    studioMode,
  ])

  return (
    <AppConnectionContext.Provider value={value}>
      {changingSurface ? null : children}
    </AppConnectionContext.Provider>
  )
}
