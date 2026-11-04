import type { StudioEntryAccess } from '#/platform/entry/resolve-entry'
import type {
  ServerMode,
  ConnectionRole,
  ServerAuthMode,
  StudioMode,
} from '#/platform/studio-contract'
import { resolveDataAccess } from '#/platform/connection/configuration'
import type { ConnectionDraft } from '#/platform/connection/types'
import { readServerIdentity } from './health'
import { connectionProfileFingerprint } from './profiles'

export type VerifiedPrincipal = Readonly<{
  accountId: string
  userId: string
  role: ConnectionRole
}>
export type IdentityVerification =
  | { status: 'unchecked' }
  | { status: 'verified'; principal: VerifiedPrincipal }

/** Ephemeral proof; never restored from persisted profile labels or authMode. */
export type VerifiedConnection = Readonly<{
  configuration: Readonly<ConnectionDraft>
  serverMode: ServerAuthMode
  data: IdentityVerification
  control: IdentityVerification
}>

const proofs = new WeakMap<VerifiedConnection, string>()

export function createVerifiedConnection(
  configuration: ConnectionDraft,
  serverMode: ServerAuthMode,
  checked: {
    data?: Record<string, unknown>
    control?: Record<string, unknown>
  },
): VerifiedConnection {
  const identity = (health?: Record<string, unknown>): IdentityVerification => {
    if (!health) return Object.freeze({ status: 'unchecked' })
    const role =
      health.role === 'root' ||
      health.role === 'admin' ||
      health.role === 'user'
        ? health.role
        : 'unknown'
    return Object.freeze({
      status: 'verified',
      principal: Object.freeze({ ...readServerIdentity(health), role }),
    })
  }
  const result: VerifiedConnection = Object.freeze({
    configuration: Object.freeze({ ...configuration }),
    serverMode,
    data: identity(checked.data),
    control: identity(checked.control),
  })
  proofs.set(result, connectionProfileFingerprint(configuration))
  return result
}

export function isCurrentVerification(
  result: VerifiedConnection,
  configuration = result.configuration,
): boolean {
  return (
    proofs.has(result) &&
    proofs.get(result) === connectionProfileFingerprint(configuration)
  )
}

export type ConnectionAccessContext = {
  surface: StudioMode
  entryAccess: StudioEntryAccess
  dataAccess: ReturnType<typeof resolveDataAccess>
  dataIdentity: { accountId: string; userId: string }
  managementAccountId: string
  data: IdentityVerification
  control: IdentityVerification
}

/** Converts connection state into the only regions a landing decision may select. */
export function resolveConnectionEntryAccess({
  connection,
  serverMode,
  canManage,
  role,
}: {
  connection: ConnectionDraft
  serverMode?: ServerMode
  canManage: boolean
  role?: ConnectionRole
}): StudioEntryAccess {
  if (!canManage) return 'workbench'
  if (
    (serverMode === 'api_key' && role === 'root') ||
    resolveDataAccess(connection, serverMode) === 'control-only'
  )
    return 'management'
  return 'both'
}
