import {
  resolveConfiguredServiceProvider,
  identifyServiceProvider,
} from '#/platform/connection/service-provider'
import { resolveDataAccess } from '#/platform/connection/configuration'
import { isStudioConnectionAllowed } from '#/platform/deployment/experience'
import { normalizeOvClientError, ovClient } from '#/shared/api/ov-client/index'
import i18n from '#/i18n/core'
import type {
  ConnectionSnapshot,
  GeneratedCredential,
} from './connection-contract'
import {
  applyConnection,
  AUTH_PROMPT_SUPPRESSION_MS,
  connectionChangedError,
  createConnectionError,
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
} from './connection-runtime'
import type { ConnectionDraft, ConnectionRole } from './types'
import type { ServerMode } from './health'
import {
  fetchServerHealth,
  normalizeBaseUrl,
  readServerIdentity,
  resetServerHealthCache,
  resolveServerAuthMode,
} from './health'
import type { ConnectionProfiles } from './profiles'
import {
  activateIdentityProfile,
  connectionProfileFingerprint,
  isValidConnectionUrl,
  normalizeProfileConnection,
  persistConnectionProfiles,
  readConnectionProfiles,
  updateActiveProfile,
} from './profiles'
import {
  assertAllowedConnection,
  shouldDiscoverGatewayHost,
} from './connection-policy'
import { createConnectionHealthHeaders } from './auth'
import { createProfileCommands } from './connection-operations'
import { rememberSurface, restoreSurface } from './surface-context'
import type { StudioMode } from './studio-mode'
import {
  canOpenManagement,
  connectionSettingsPath,
  isConnectionSettingsPath,
  isSharedStudioPath,
  readRememberedStudioMode,
  rememberStudioMode,
  resolveStudioMode,
} from './studio-mode'
import type {
  ConnectionAccessContext,
  VerifiedPrincipal,
} from './connection-identity'
import { resolveConnectionEntryAccess } from './connection-identity'

export type ConnectionNavigation = {
  to:
    | '/home'
    | '/filesystem'
    | '/admin'
    | '/settings'
    | '/admin/settings'
    | '/directory'
    | '/memories'
  replace?: boolean
  search?: { upload: false }
}
type Dependencies = {
  clearCache: () => void
  navigate: (destination: ConnectionNavigation) => void | Promise<void>
}
type IdentityProof = { key: string; principal: VerifiedPrincipal }

/** Owns connection lifecycle, cancellation and published state independently of React. */
export class ConnectionController {
  private profiles: ConnectionProfiles
  private connection: ConnectionDraft
  private surface: StudioMode
  private pathname: string
  private serverMode: ServerMode = 'checking'
  private health: Record<string, unknown> | null = null
  private identities: Partial<Record<'data' | 'control', IdentityProof>> = {}
  private roleLoading = true
  private gateway = false
  private openvikingUrl = ''
  private generated: GeneratedCredential | null = null
  private generation = 0
  private revision = 0
  private request: AbortController | null = null
  private mounted = false
  private suppressAuthUntil = 0
  private pendingProfileEntry = false
  private hostDiscovery: Promise<Record<string, unknown> | null> | null = null
  private listeners = new Set<() => void>()
  private snapshot: ConnectionSnapshot
  private navigate: Dependencies['navigate']

  constructor(
    pathname: string,
    private readonly dependencies: Dependencies,
  ) {
    this.pathname = pathname
    this.navigate = dependencies.navigate
    dependencies.clearCache()
    this.profiles = readConnectionProfiles(readInitialConnection())
    this.surface = resolveStudioMode(pathname)
    this.connection = normalizeConnectionDraft(
      restoreSurface(this.activeProfile(), this.surface),
    )
    assertAllowedConnection(this.connection)
    applyConnection(this.connection, this.serverMode, this.surface)
    this.snapshot = this.createSnapshot()
  }

  getSnapshot = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  setNavigation(navigate: Dependencies['navigate']) {
    this.navigate = navigate
  }
  private activeProfile() {
    return this.profiles.profiles.find(
      (profile) => profile.id === this.profiles.activeId,
    )!
  }
  private modeForPath(pathname: string) {
    return resolveStudioMode(
      pathname,
      readRememberedStudioMode(
        this.profiles.activeId,
        this.connection.adminApiKey && !this.connection.apiKey
          ? 'management'
          : 'workbench',
      ),
    )
  }
  /** Restrict the imperative client before route children can issue requests. */
  prepareRoute(pathname: string) {
    const mode = this.modeForPath(pathname)
    const changing = mode !== this.surface
    const connection = changing
      ? restoreSurface(
          rememberSurface(this.activeProfile(), this.connection, this.surface),
          mode,
        )
      : this.connection
    applyConnection(connection, changing ? 'checking' : this.serverMode, mode)
    return changing
  }
  setRoute(pathname: string) {
    this.pathname = pathname
    const mode = this.modeForPath(pathname)
    if (mode !== this.surface) {
      const previous = rememberSurface(
        this.activeProfile(),
        this.connection,
        this.surface,
      )
      const next = restoreSurface(previous, mode)
      this.writeProfiles({
        ...this.profiles,
        profiles: this.profiles.profiles.map((profile) =>
          profile.id === previous.id
            ? { ...previous, connection: next }
            : profile,
        ),
      })
      this.surface = mode
      this.replaceConnection(next)
    }
    if (pathname !== '/' && !isSharedStudioPath(pathname))
      rememberStudioMode(this.profiles.activeId, mode)
    this.finishProfileEntry()
  }
  start() {
    this.mounted = true
    this.persistCurrent()
    this.beginProbe()
    const interceptor = ovClient.instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (
          shouldRedirectToLoginOnApiError(error) &&
          Date.now() >= this.suppressAuthUntil
        )
          this.openSettings()
        return Promise.reject(error)
      },
    )
    return () => {
      this.mounted = false
      this.generation++
      this.revision++
      this.request?.abort()
      ovClient.instance.interceptors.response.eject(interceptor)
    }
  }
  private publish() {
    this.snapshot = this.createSnapshot()
    this.listeners.forEach((listener) => listener())
  }
  private writeProfiles(next: ConnectionProfiles) {
    persistConnectionProfiles(next)
    this.profiles = next
  }
  private persistCurrent() {
    persistConnection(this.connection)
    this.writeProfiles(updateActiveProfile(this.profiles, this.connection))
  }
  private openSettings = () => {
    const to = connectionSettingsPath(this.pathname, this.surface)
    if (this.pathname !== to) void this.navigate({ to })
  }
  private currentRole(): ConnectionRole {
    if (this.serverMode === 'checking' || this.serverMode === 'offline')
      return 'unknown'
    if (this.serverMode === 'dev') return 'root'
    return (
      this.currentIdentity(this.surface === 'management' ? 'control' : 'data')
        ?.principal.role ?? 'unknown'
    )
  }
  private currentIdentity(scope: 'data' | 'control') {
    const proof = this.identities[scope]
    return this.serverMode !== 'checking' &&
      this.serverMode !== 'offline' &&
      proof?.key ===
        connectionProfileFingerprint(this.connection) + this.serverMode
      ? proof
      : undefined
  }
  private recordIdentity(
    scope: 'data' | 'control',
    principal: VerifiedPrincipal,
  ) {
    if (principal.role !== 'unknown')
      this.identities = {
        ...this.identities,
        [scope]: {
          key: connectionProfileFingerprint(this.connection) + this.serverMode,
          principal,
        },
      }
  }
  private reconnect = () => {
    this.revision++
    this.suppressAuthUntil = Date.now() + AUTH_PROMPT_SUPPRESSION_MS
    resetServerHealthCache()
    this.identities = {}
    this.dependencies.clearCache()
    this.beginProbe()
  }
  private replaceConnection(next: ConnectionDraft) {
    assertAllowedConnection(next)
    this.generation++
    this.revision++
    this.connection = synchronizeConnectionRuntime(
      next,
      'checking',
      this.surface,
    )
    this.persistCurrent()
    this.dependencies.clearCache()
    if (this.mounted) this.beginProbe()
    else {
      this.serverMode = 'checking'
      this.health = null
      this.roleLoading = true
      this.publish()
    }
  }
  private beginProbe() {
    this.request?.abort()
    const request = new AbortController()
    this.request = request
    this.serverMode = 'checking'
    this.health = null
    this.roleLoading = true
    applyConnection(this.connection, this.serverMode, this.surface)
    this.publish()
    void this.probe(request).catch(() => {
      if (this.request !== request || request.signal.aborted) return
      this.serverMode = 'offline'
      this.roleLoading = false
      this.publish()
    })
  }
  private async probe(request: AbortController) {
    const { signal } = request
    const current = () =>
      !signal.aborted && this.mounted && this.request === request
    let connection = this.connection
    const surface = this.surface
    if (
      isBrowser() &&
      shouldDiscoverGatewayHost(window.location.pathname) &&
      normalizeBaseUrl(connection.baseUrl) !== window.location.origin
    ) {
      this.hostDiscovery ??= fetchServerHealth(window.location.origin).catch(
        () => null,
      )
      const host = await this.hostDiscovery
      if (!current()) return
      if (host?.service === 'openviking-gateway') {
        this.gateway = true
        this.openvikingUrl =
          typeof host.openviking_url === 'string' ? host.openviking_url : ''
        this.replaceConnection({
          ...connection,
          serviceSelection: 'opensource',
          baseUrl: window.location.origin,
          apiKey: '',
          adminApiKey: '',
          accountId: 'default',
          userId: 'default',
        })
        return
      }
    }
    let health: Record<string, unknown>
    try {
      try {
        health = await fetchServerHealth(
          connection.baseUrl,
          createConnectionHealthHeaders(
            connection,
            surface === 'management' ? 'control' : 'data',
          ),
          signal,
        )
      } catch (error) {
        if (signal.aborted || !connection.adminApiKey) throw error
        const discovery = await fetchServerHealth(
          connection.baseUrl,
          {},
          signal,
        ).catch(() => null)
        if (!discovery || resolveServerAuthMode(discovery) !== 'trusted')
          throw error
        health = discovery
      }
      if (
        resolveServerAuthMode(health) === 'trusted' &&
        (connection.adminApiKey || connection.apiKey)
      )
        health = await fetchServerHealth(
          connection.baseUrl,
          createConnectionHealthHeaders(
            connection,
            surface === 'management' ? 'control' : 'data',
            'trusted',
          ),
          signal,
        )
    } catch (error) {
      if (current()) {
        this.serverMode = 'offline'
        this.roleLoading = false
        this.publish()
        if (shouldRedirectToLoginOnApiError(normalizeOvClientError(error)))
          this.openSettings()
      }
      return
    }
    if (!current()) return
    this.gateway = health.service === 'openviking-gateway'
    this.openvikingUrl =
      typeof health.openviking_url === 'string' ? health.openviking_url : ''
    const mode = resolveServerAuthMode(health)
    const dataKey = surface !== 'management' || !connection.adminApiKey
    const resolvedData = dataKey
      ? synchronizeResolvedDataIdentity(connection, {
          ...readServerIdentity(health),
          role: isConnectionRole(health.role) ? health.role : 'unknown',
        })
      : null
    const classified =
      mode === 'trusted' &&
      (connection.adminApiKey || connection.apiKey) &&
      connection.apiKey
        ? {
            ...connection,
            adminApiKey: connection.adminApiKey || connection.apiKey,
            apiKey: '',
          }
        : dataKey &&
            mode !== 'trusted' &&
            identifyServiceProvider(connection.baseUrl) === 'opensource' &&
            connection.serviceSelection !== 'volcengine'
          ? health.role === 'root' && connection.apiKey
            ? { ...connection, adminApiKey: connection.apiKey, apiKey: '' }
            : health.role === 'admin' &&
                !connection.adminApiKey &&
                connection.apiKey
              ? {
                  ...(resolvedData ?? connection),
                  adminApiKey: connection.apiKey,
                }
              : null
          : null
    const resolved = classified ?? resolvedData
    if (resolved) {
      this.generation++
      this.connection = synchronizeConnectionRuntime(resolved, mode, surface)
      this.persistCurrent()
      this.dependencies.clearCache()
      connection = this.connection
    }
    this.serverMode = mode
    this.health = health
    const management = surface === 'management'
    const scope = management ? 'control' : 'data'
    const roleProbe = resolveConnectionRoleProbeState({
      apiKey:
        management || mode === 'trusted'
          ? connection.adminApiKey || connection.apiKey
          : connection.apiKey,
      baseUrl: connection.baseUrl,
      serverMode: mode,
    })
    delete this.identities[scope]
    this.roleLoading = roleProbe.isLoading
    applyConnection(connection, mode, surface)
    this.publish()
    if (roleProbe.shouldProbe) {
      const principal = await detectConnectionIdentity(
        connection,
        scope,
        signal,
        mode,
      ).catch(() => ({ accountId: '', userId: '', role: 'unknown' as const }))
      if (!current()) return
      if (
        management &&
        principal.role === 'admin' &&
        principal.accountId &&
        (connection.accountId !== principal.accountId ||
          (mode === 'api_key' &&
            !connection.apiKey &&
            Boolean(principal.userId)))
      ) {
        this.writeProfiles({
          ...this.profiles,
          profiles: this.profiles.profiles.map((profile) =>
            profile.id === this.profiles.activeId &&
            !profile.workbenchIdentity &&
            connection.apiKey
              ? rememberSurface(profile, connection, 'workbench')
              : profile,
          ),
        })
        this.generation++
        this.connection = synchronizeConnectionRuntime(
          {
            ...connection,
            accountId: principal.accountId,
            ...(mode === 'api_key' && principal.userId
              ? { apiKey: connection.adminApiKey, userId: principal.userId }
              : {}),
          },
          mode,
          surface,
        )
        this.persistCurrent()
        this.dependencies.clearCache()
      }
      this.recordIdentity(scope, principal)
      this.roleLoading = false
      this.publish()
    }
    this.finishProfileEntry()
  }
  private finishProfileEntry() {
    if (
      !this.pendingProfileEntry ||
      this.serverMode === 'checking' ||
      this.serverMode === 'offline' ||
      this.roleLoading ||
      this.pathname.includes('/connections/')
    )
      return
    this.pendingProfileEntry = false
    const allowed = canOpenManagement({
      provider: resolveConfiguredServiceProvider(
        this.connection.baseUrl,
        this.connection.serviceSelection,
      ),
      connection: this.connection,
      role: this.currentRole(),
      isRoleLoading: false,
      serverMode: this.serverMode,
    })
    const target =
      this.surface === 'management' && !allowed
        ? 'workbench'
        : this.surface === 'workbench' &&
            resolveDataAccess(this.connection, this.serverMode) ===
              'control-only' &&
            this.connection.adminApiKey
          ? 'management'
          : this.surface
    if (target === this.surface) return
    void this.navigate({
      replace: true,
      to: isConnectionSettingsPath(this.pathname)
        ? target === 'management'
          ? '/admin/settings'
          : '/settings'
        : target === 'management'
          ? '/admin'
          : resolveConfiguredServiceProvider(
                this.connection.baseUrl,
                this.connection.serviceSelection,
              ) === 'volcengine'
            ? '/filesystem'
            : '/home',
    })
  }
  private accessContext(): ConnectionAccessContext {
    const workbench = restoreSurface(this.activeProfile(), 'workbench')
    const identity = (scope: 'data' | 'control') => {
      const proof = this.currentIdentity(scope)
      return proof
        ? { status: 'verified' as const, principal: proof.principal }
        : { status: 'unchecked' as const }
    }
    const role = this.currentRole()
    return {
      surface: this.surface,
      entryAccess: resolveConnectionEntryAccess({
        connection: this.connection,
        serverMode: this.serverMode,
        role,
        canManage: canOpenManagement({
          connection: this.connection,
          serverMode: this.serverMode,
          role,
          isRoleLoading: this.roleLoading,
          provider: resolveConfiguredServiceProvider(
            this.connection.baseUrl,
            this.connection.serviceSelection,
          ),
        }),
      }),
      dataAccess: resolveDataAccess(this.connection, this.serverMode),
      dataIdentity:
        this.surface === 'workbench'
          ? {
              accountId: this.connection.accountId,
              userId: this.connection.userId,
            }
          : { accountId: workbench.accountId, userId: workbench.userId },
      managementAccountId:
        this.surface === 'management'
          ? this.connection.accountId
          : (this.activeProfile().managementAccountId ??
            this.connection.accountId),
      data: identity('data'),
      control: identity('control'),
    }
  }
  private commit(
    next: ConnectionDraft,
    preserveIdentity = false,
    rememberIdentity = false,
    dataSelection = false,
  ) {
    assertAllowedConnection(next)
    this.suppressAuthUntil = Date.now() + AUTH_PROMPT_SUPPRESSION_MS
    const normalized = normalizeConnectionDraft(next)
    if (preserveIdentity)
      this.writeProfiles(activateIdentityProfile(this.profiles, normalized))
    else if (rememberIdentity)
      this.writeProfiles(updateActiveProfile(this.profiles, normalized, true))
    this.writeProfiles({
      ...this.profiles,
      profiles: this.profiles.profiles.map((profile) =>
        profile.id === this.profiles.activeId
          ? rememberSurface(
              profile,
              normalized,
              dataSelection ? 'workbench' : this.surface,
            )
          : profile,
      ),
    })
    this.replaceConnection(normalized)
  }
  private createSnapshot(): ConnectionSnapshot {
    const generation = this.generation
    const profileId = this.profiles.activeId
    const assertCurrent = () => {
      if (
        generation !== this.generation ||
        profileId !== this.profiles.activeId
      )
        throw connectionChangedError()
    }
    const commands = createProfileCommands({
      read: () => this.profiles,
      write: (next) => this.writeProfiles(next),
      assertCurrent,
      activate: (next) => {
        this.pendingProfileEntry = true
        this.generated = null
        this.identities = {}
        resetServerHealthCache()
        this.suppressAuthUntil = Date.now() + AUTH_PROMPT_SUPPRESSION_MS
        this.replaceConnection(
          restoreSurface(
            { ...this.activeProfile(), connection: next },
            this.surface,
          ),
        )
      },
    })
    return {
      ...commands,
      saveProfile: (profile) => {
        commands.saveProfile(profile)
        this.publish()
      },
      deleteProfile: (id) => {
        commands.deleteProfile(id)
        this.publish()
      },
      activateProfile: (id) => {
        commands.activateProfile(id)
        this.publish()
      },
      submitProfile: async (profile, options) => {
        try {
          await commands.submitProfile(profile, options)
        } finally {
          this.publish()
        }
      },
      accessContext: this.accessContext(),
      profiles: this.profiles.profiles.filter((profile) =>
        isStudioConnectionAllowed(profile.connection),
      ),
      activeProfileId: this.profiles.activeId,
      connection: this.connection,
      connectionRole: this.currentRole(),
      generatedCredential: this.generated,
      identityScopeKey:
        this.profiles.activeId +
        '\u0000' +
        createIdentityScopeKey(this.connection, this.serverMode),
      isConnectionRoleLoading: this.roleLoading,
      isGatewayHost: this.gateway,
      openvikingUrl: this.openvikingUrl,
      openConnectionSettings: this.openSettings,
      reconnect: this.reconnect,
      serverHealth: this.health,
      serverMode: this.serverMode,
      studioMode: this.surface,
      clearGeneratedCredential: () => {
        this.generated = null
        this.publish()
      },
      setGeneratedCredential: (credential) => {
        this.generated = credential
        this.publish()
      },
      saveConnection: (next) => {
        assertCurrent()
        assertAllowedConnection(next)
        if (!isValidConnectionUrl(next.baseUrl))
          throw new Error(i18n.t('studio:profiles.invalidUrl'))
        this.commit(normalizeProfileConnection(next))
      },
      switchIdentity: async ({
        accountId,
        allowLegacyIdentityFallback,
        preserveProfile = true,
        rememberWorkbench = true,
        signal,
        apiKey,
        userId,
      }) => {
        assertCurrent()
        const mode = this.serverMode
        if (mode === 'dev' || mode === 'checking')
          throw createConnectionError('identitySwitchUnsupported')
        const revision = ++this.revision
        const check = () => {
          signal?.throwIfAborted()
          assertCurrent()
          if (revision !== this.revision) throw connectionChangedError()
        }
        const requested = normalizeConnectionDraft({
          ...this.connection,
          accountId,
          apiKey: mode === 'trusted' ? '' : apiKey,
          userId,
        })
        const identity = await detectConnectionIdentity(
          requested,
          'data',
          signal,
          mode,
        )
        check()
        const resolved = resolveSwitchedIdentity(
          requested,
          identity,
          allowLegacyIdentityFallback,
        )
        if (!resolved) throw createConnectionError('credentialMismatch')
        if (this.pathname === '/directory' || this.pathname === '/memories')
          await this.navigate({ replace: true, to: this.pathname })
        if (this.pathname === '/filesystem')
          await this.navigate({
            replace: true,
            to: '/filesystem',
            search: { upload: false },
          })
        check()
        this.commit(
          { ...requested, ...resolved },
          !preserveProfile,
          preserveProfile,
          rememberWorkbench,
        )
      },
      switchManagementAccount: async (accountId) => {
        assertCurrent()
        if (this.currentRole() !== 'root')
          throw createConnectionError('rootRequired')
        this.writeProfiles({
          ...this.profiles,
          profiles: this.profiles.profiles.map((profile) =>
            profile.id === this.profiles.activeId
              ? rememberSurface(profile, this.connection, this.surface)
              : profile,
          ),
        })
        this.commit(
          this.serverMode === 'trusted'
            ? {
                ...this.connection,
                accountId: accountId.trim(),
                userId: 'default',
              }
            : createManagementAccountConnection(
                this.connection,
                accountId,
                this.gateway,
              ),
          false,
          true,
        )
      },
    }
  }
}
