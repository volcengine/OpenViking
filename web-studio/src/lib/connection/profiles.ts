import type { ConnectionDraft } from '#/lib/connection/types'
import { resolveConfiguredServiceProvider } from '../studio-service'

export const PROFILES_STORAGE_KEY = 'ov_console_connection_profiles'

export type ConnectionProfile = {
  id: string
  name: string
  connection: ConnectionDraft
}
export type ConnectionProfiles = {
  activeId: string
  profiles: Array<ConnectionProfile>
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
      localStorage.getItem(PROFILES_STORAGE_KEY) || 'null',
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
        .map((profile) => ({
          ...profile,
          connection: normalizeProfileConnection(profile.connection),
        }))
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
    localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify(value))
  } catch {
    // Match the existing connection persistence in restricted browsers.
  }
}

export function updateActiveProfile(
  value: ConnectionProfiles,
  connection: ConnectionDraft,
): ConnectionProfiles {
  return {
    ...value,
    profiles: value.profiles.map((profile) =>
      profile.id === value.activeId
        ? { ...profile, connection: normalizeProfileConnection(connection) }
        : profile,
    ),
  }
}
