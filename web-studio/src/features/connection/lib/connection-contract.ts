import type { ConnectionAccessContext } from './connection-identity'
import type { ProfileSubmission } from './connection-operations'
import type { ConnectionProfile } from './profiles'
import type { ConnectionDraft, ConnectionRole } from './types'
import type { StudioMode, ServerMode } from '#/platform/studio-contract'

export type ConnectionIdentitySummary = {
  labelKey: string
  values?: {
    identity?: string
  }
}

export type GeneratedCredential = {
  accountId?: string
  apiKey: string
  userId?: string
}

export type ConnectionSnapshot = {
  accessContext: ConnectionAccessContext
  profiles: Array<ConnectionProfile>
  activeProfileId: string
  saveProfile: (profile: ConnectionProfile) => void
  submitProfile: (
    profile: ConnectionProfile,
    options: ProfileSubmission,
  ) => Promise<void>
  activateProfile: (id: string) => void
  deleteProfile: (id: string) => void
  clearGeneratedCredential: () => void
  connection: ConnectionDraft
  connectionRole: ConnectionRole
  generatedCredential: GeneratedCredential | null
  identityScopeKey: string
  isConnectionRoleLoading: boolean
  isGatewayHost: boolean
  openvikingUrl: string
  openConnectionSettings: () => void
  reconnect: () => void
  serverHealth: Record<string, unknown> | null
  saveConnection: (next: ConnectionDraft) => void
  setGeneratedCredential: (credential: GeneratedCredential) => void
  serverMode: ServerMode
  studioMode: StudioMode
  switchIdentity: (identity: {
    accountId: string
    allowLegacyIdentityFallback?: boolean
    /** Keep the selected connection configuration while changing its scope. */
    preserveProfile?: boolean
    /** A management-only resource identity must not replace the user's workspace. */
    rememberWorkbench?: boolean
    signal?: AbortSignal
    apiKey: string
    userId: string
  }) => Promise<void>
  switchManagementAccount: (
    accountId: string,
    options?: { preserveProfile?: boolean },
  ) => Promise<void>
}

/** Compatibility name for the full internal React context. */
export type AppConnectionContextValue = ConnectionSnapshot
