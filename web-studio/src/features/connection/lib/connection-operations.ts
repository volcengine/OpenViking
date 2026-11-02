import { normalizeConnectionDraft } from '#/platform/connection/configuration'
import type { ConnectionDraft } from '#/platform/connection/types'
import { resolveStudioEntry } from '#/platform/entry/resolve-entry'
import { resolveConfiguredServiceProvider } from '#/platform/connection/service-provider'
import i18n from '#/i18n/core'
import { readPreferredMode } from './studio-mode'
import { completeStudioSetup } from '#/features/connection/lib/profile-bootstrap'
import {
  verifyConnection,
  verifyStudioSetup,
} from '#/features/connection/lib/connection-verification'
import {
  StudioSetupError,
  StudioSetupRequestError,
} from '#/features/connection/lib/connection-errors'
import type { ConnectionProfile, ConnectionProfiles } from './profiles'
import {
  connectionProfileFingerprint,
  normalizeProfileConnection,
  isValidConnectionUrl,
} from './profiles'
import { assertAllowedConnection } from './connection-policy'
import { flushCredentialStorage } from './credential-storage'
import type { VerifiedConnection } from './connection-identity'
import {
  isCurrentVerification,
  resolveConnectionEntryAccess,
} from './connection-identity'

export type ConnectionLoginResult = {
  profileId: string
  verified: VerifiedConnection
  destination: ReturnType<typeof resolveStudioEntry>
}

export type ProfileSubmission = {
  signal: AbortSignal
  selectedIdentity?: { accountId: string; userId: string }
}

/** Owns editor verification and persistence ordering, including metadata-only edits. */
export async function submitConnectionProfile(
  draft: ConnectionProfile,
  original: ConnectionProfile | undefined,
  options: ProfileSubmission,
  commit: (profile: ConnectionProfile, activate: boolean) => void,
) {
  let connection = normalizeProfileConnection(draft.connection)
  assertAllowedConnection(connection)
  const changed =
    !original ||
    connectionProfileFingerprint(original.connection) !==
      connectionProfileFingerprint(connection)
  if (
    !original ||
    (changed && !(options.selectedIdentity && !connection.apiKey.trim())) ||
    (options.selectedIdentity && connection.apiKey.trim())
  ) {
    try {
      connection = await verifyStudioSetup(connection, options.signal)
    } catch (cause) {
      throw cause instanceof StudioSetupError ||
        cause instanceof StudioSetupRequestError
        ? cause
        : new StudioSetupRequestError('data', cause, connection)
    }
    if (
      options.selectedIdentity &&
      (connection.accountId !== options.selectedIdentity.accountId ||
        connection.userId !== options.selectedIdentity.userId)
    )
      throw new StudioSetupError('directoryIdentityMismatch')
  }
  options.signal.throwIfAborted()
  assertAllowedConnection(connection)
  commit(
    {
      ...draft,
      connection: {
        ...connection,
        adminApiKey:
          connection.serviceSelection === 'volcengine'
            ? ''
            : connection.adminApiKey,
      },
    },
    !original,
  )
  try {
    await flushCredentialStorage()
  } catch {
    throw new Error(i18n.t('studio:credentialStorage.saveError'))
  }
  options.signal.throwIfAborted()
}

export async function loginAndActivate(
  draft: ConnectionDraft,
  options: { name: string; signal: AbortSignal; profileId?: string },
): Promise<ConnectionLoginResult> {
  const verified = await verifyConnection(draft, options.signal)
  options.signal.throwIfAborted()
  if (!isCurrentVerification(verified))
    throw new Error(i18n.t('onboarding:verifyFailed'))
  const connection = verified.configuration
  const profileId = await completeStudioSetup(
    connection,
    options.name,
    options.profileId,
  )
  options.signal.throwIfAborted()
  const controlRole =
    verified.control.status === 'verified'
      ? verified.control.principal.role
      : 'unknown'
  const role =
    verified.data.status === 'verified'
      ? verified.data.principal.role
      : controlRole
  const destination = resolveStudioEntry({
    access: resolveConnectionEntryAccess({
      connection,
      serverMode: verified.serverMode,
      role,
      canManage:
        controlRole === 'root' ||
        controlRole === 'admin' ||
        verified.serverMode === 'trusted',
    }),
    provider: resolveConfiguredServiceProvider(
      connection.baseUrl,
      connection.serviceSelection,
    ),
    preferredMode:
      verified.serverMode === 'api_key'
        ? null
        : readPreferredMode(connection, profileId),
  })
  return { profileId, verified, destination }
}

export function createProfileCommands(runtime: {
  read: () => ConnectionProfiles
  write: (state: ConnectionProfiles) => void
  activate: (connection: ConnectionDraft) => void
  assertCurrent: () => void
}) {
  const saveProfile = (profile: ConnectionProfile) => {
    runtime.assertCurrent()
    assertAllowedConnection(profile.connection)
    if (!isValidConnectionUrl(profile.connection.baseUrl))
      throw new Error(i18n.t('studio:profiles.invalidUrl'))
    const previous = runtime
      .read()
      .profiles.find((entry) => entry.id === profile.id)
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
    const current = runtime.read()
    const exists = current.profiles.some((p) => p.id === profile.id)
    runtime.write({
      ...current,
      profiles: exists
        ? current.profiles.map((p) => (p.id === profile.id ? normalized : p))
        : [...current.profiles, normalized],
    })
    if (profile.id === current.activeId) runtime.activate(normalized.connection)
  }
  const activateProfile = (id: string) => {
    runtime.assertCurrent()
    const current = runtime.read()
    const profile = current.profiles.find((p) => p.id === id)
    if (!profile || id === current.activeId) return
    assertAllowedConnection(profile.connection)
    runtime.write({ ...current, activeId: id })
    runtime.activate(profile.connection)
  }

  return {
    saveProfile,
    activateProfile,
    deleteProfile(id: string) {
      runtime.assertCurrent()
      const current = runtime.read()
      if (id !== current.activeId)
        runtime.write({
          ...current,
          profiles: current.profiles.filter((profile) => profile.id !== id),
        })
    },
    async submitProfile(
      profile: ConnectionProfile,
      options: ProfileSubmission,
    ) {
      runtime.assertCurrent()
      const original = runtime
        .read()
        .profiles.find((entry) => entry.id === profile.id)
      await submitConnectionProfile(
        profile,
        original,
        options,
        (next, activate) => {
          runtime.assertCurrent()
          saveProfile(next)
          if (activate) activateProfile(next.id)
        },
      )
    },
  }
}
