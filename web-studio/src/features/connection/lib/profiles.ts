import type { WorkbenchIdentity } from '#/features/connection/lib/surface-context'
import {
  readCredentialStorage,
  writeCredentialStorage,
} from '#/features/connection/lib/credential-storage'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'

export const PROFILES_STORAGE_KEY = 'ov_console_connection_profiles'

export type SavedDataIdentity = Pick<
  ConnectionDraft,
  'baseUrl' | 'serviceSelection' | 'accountId' | 'userId' | 'apiKey'
>

function isSavedDataIdentity(value: unknown): value is SavedDataIdentity {
  if (!value || typeof value !== 'object') return false
  const entry = value as Record<string, unknown>
  return (
    (entry.serviceSelection === undefined ||
      entry.serviceSelection === 'auto' ||
      entry.serviceSelection === 'opensource' ||
      entry.serviceSelection === 'volcengine') &&
    typeof entry.baseUrl === 'string' &&
    isValidConnectionUrl(entry.baseUrl) &&
    ['baseUrl', 'accountId', 'userId', 'apiKey'].every(
      (key) => typeof entry[key] === 'string' && Boolean(entry[key].trim()),
    )
  )
}

function uniqueDataIdentities(values: readonly unknown[]): SavedDataIdentity[] {
  const seen = new Set<string>()
  return values.filter(isSavedDataIdentity).flatMap((entry) => {
    const provider = resolveConfiguredServiceProvider(
      entry.baseUrl,
      entry.serviceSelection,
    )
    if (provider === 'unknown') return []
    const normalized: SavedDataIdentity = {
      baseUrl: entry.baseUrl.trim(),
      serviceSelection: provider,
      accountId: entry.accountId.trim(),
      userId: entry.userId.trim(),
      apiKey: entry.apiKey.trim(),
    }
    const key = JSON.stringify([
      normalized.serviceSelection,
      normalized.baseUrl.replace(/\/+$/, ''),
      normalized.accountId,
      normalized.userId,
      normalized.apiKey,
    ])
    if (seen.has(key)) return []
    seen.add(key)
    return [normalized]
  })
}

/** Local credentials only; never discovers identities from a server directory. */
export function savedDataIdentities(
  profiles: readonly ConnectionProfile[],
  connection: ConnectionDraft,
): SavedDataIdentity[] {
  const provider = resolveConfiguredServiceProvider(
    connection.baseUrl,
    connection.serviceSelection,
  )
  const url = connection.baseUrl.trim().replace(/\/+$/, '')
  return uniqueDataIdentities([
    connection,
    ...profiles.flatMap((profile) => [
      profile.connection,
      ...(Array.isArray(profile.identities) ? profile.identities : []),
    ]),
  ]).filter(
    (identity) =>
      identity.baseUrl.replace(/\/+$/, '') === url &&
      identity.serviceSelection === provider,
  )
}

export type ConnectionProfile = {
  id: string
  name: string
  connection: ConnectionDraft
  identities?: SavedDataIdentity[]
  workbenchIdentity?: WorkbenchIdentity
  managementAccountId?: string
}
export type ConnectionProfiles = {
  activeId: string
  profiles: Array<ConnectionProfile>
}

/** Service navigation groups credentials without changing the saved profiles. */
export function groupProfilesByService(
  profiles: readonly ConnectionProfile[],
): ConnectionProfile[][] {
  const groups = new Map<string, ConnectionProfile[]>()
  for (const profile of profiles) {
    const connection = normalizeProfileConnection(profile.connection)
    const key = JSON.stringify([
      connection.serviceSelection,
      connection.baseUrl.trim().replace(/\/+$/, ''),
    ])
    const group = groups.get(key)
    if (group) group.push(profile)
    else groups.set(key, [profile])
  }
  return [...groups.values()]
}

export function isValidConnectionUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
    )
  } catch {
    return false
  }
}

// Display and runtime must use the same provider, including official hosted URLs.
export function normalizeProfileConnection(
  connection: ConnectionDraft,
): ConnectionDraft {
  const provider = resolveConfiguredServiceProvider(
    connection.baseUrl,
    connection.serviceSelection,
  )
  return {
    ...connection,
    serviceSelection: provider === 'volcengine' ? 'volcengine' : 'opensource',
  }
}

export function connectionProfileFingerprint(
  connection: ConnectionDraft,
): string {
  const normalized = normalizeProfileConnection(connection)
  return JSON.stringify([
    normalized.serviceSelection,
    normalized.baseUrl.trim().replace(/\/+$/, ''),
    normalized.accountId,
    normalized.userId,
    normalized.apiKey,
    normalized.adminApiKey,
  ])
}

export function readConnectionProfiles(
  fallback: ConnectionDraft,
): ConnectionProfiles {
  try {
    const raw: unknown = JSON.parse(
      readCredentialStorage(PROFILES_STORAGE_KEY) || 'null',
    )
    if (
      raw &&
      typeof raw === 'object' &&
      'profiles' in raw &&
      'activeId' in raw &&
      Array.isArray(raw.profiles)
    ) {
      const profiles = raw.profiles.filter(
        (profile): profile is ConnectionProfile => {
          if (!profile || typeof profile !== 'object') return false
          const connection = profile.connection
          return (
            typeof profile.id === 'string' &&
            Boolean(profile.id) &&
            typeof profile.name === 'string' &&
            Boolean(profile.name.trim()) &&
            connection &&
            typeof connection === 'object' &&
            ['accountId', 'userId', 'apiKey', 'adminApiKey', 'baseUrl'].every(
              (key) => typeof connection[key] === 'string',
            ) &&
            ['opensource', 'volcengine'].includes(connection.serviceSelection)
          )
        },
      )
      const unique = profiles
        .filter(
          (profile, index) =>
            profiles.findIndex((p) => p.id === profile.id) === index,
        )
        .map((profile) => {
          const { identities, ...saved } = profile
          return {
            ...saved,
            connection: normalizeProfileConnection(profile.connection),
            ...(Array.isArray(identities)
              ? { identities: uniqueDataIdentities(identities) }
              : {}),
          }
        })
      if (unique.length) {
        return {
          profiles: unique,
          activeId: unique.some((p) => p.id === raw.activeId)
            ? (raw.activeId as string)
            : unique[0].id,
        }
      }
    }
  } catch {
    // Restricted storage or an older malformed value must not lose the legacy connection.
  }
  return {
    activeId: 'default',
    profiles: [
      {
        id: 'default',
        name: 'Default',
        connection: normalizeProfileConnection(fallback),
      },
    ],
  }
}

export function persistConnectionProfiles(value: ConnectionProfiles): void {
  try {
    writeCredentialStorage(PROFILES_STORAGE_KEY, JSON.stringify(value))
  } catch {
    // Match the existing connection persistence in restricted browsers.
  }
}

export function updateActiveProfile(
  value: ConnectionProfiles,
  connection: ConnectionDraft,
  rememberIdentity = false,
): ConnectionProfiles {
  return {
    ...value,
    profiles: value.profiles.map((profile) => {
      if (profile.id !== value.activeId) return profile
      const previous = profile.connection
      const history = Array.isArray(profile.identities)
        ? profile.identities
        : []
      const identities = uniqueDataIdentities(
        rememberIdentity ? [previous, ...history] : history,
      ).filter((identity) => {
        if (rememberIdentity || previous.apiKey === connection.apiKey)
          return true
        // Replacing/removing a configured key must not resurrect it from history.
        return (
          identity.baseUrl.replace(/\/+$/, '') !==
            previous.baseUrl.trim().replace(/\/+$/, '') ||
          identity.accountId !== previous.accountId ||
          identity.userId !== previous.userId
        )
      })
      return {
        ...profile,
        connection: normalizeProfileConnection(connection),
        ...(identities.length || profile.identities ? { identities } : {}),
      }
    }),
  }
}

// Explicit identity switches preserve the previous identity and its credentials.
// Ordinary configuration edits still update the selected profile in place.
export function activateIdentityProfile(
  value: ConnectionProfiles,
  connection: ConnectionDraft,
): ConnectionProfiles {
  const normalized = normalizeProfileConnection(connection)
  const fingerprint = connectionProfileFingerprint(normalized)
  const existing = value.profiles.find(
    (profile) =>
      connectionProfileFingerprint(profile.connection) === fingerprint,
  )
  if (existing) return { ...value, activeId: existing.id }
  const id = createRandomUuid()
  return {
    activeId: id,
    profiles: [
      ...value.profiles,
      {
        id,
        name: `${normalized.accountId}/${normalized.userId}`,
        connection: normalized,
      },
    ],
  }
}
