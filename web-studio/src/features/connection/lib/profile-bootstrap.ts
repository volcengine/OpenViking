import { isStudioConnectionAllowed } from '#/platform/deployment/experience'
import type { ConnectionDraft } from './types'
import { assertAllowedConnection } from './connection-policy'
import {
  flushCredentialStorage,
  readCredentialStorage,
  writeCredentialStorage,
} from './credential-storage'
import {
  isValidConnectionUrl,
  normalizeProfileConnection,
  connectionProfileFingerprint,
  PROFILES_STORAGE_KEY,
} from './profiles'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import i18n from '#/i18n/core'

export const SETUP_STORAGE_KEY = 'ov_studio_setup'
export const WORKSPACE_SESSION_KEY = 'ov_studio_workspace_selected'
function hasConnection(value: unknown, completed = false): boolean {
  if (!value || typeof value !== 'object') return false
  const c = value as Partial<ConnectionDraft>
  return (
    typeof c.baseUrl === 'string' &&
    isValidConnectionUrl(c.baseUrl) &&
    (completed ||
      Boolean(
        (typeof c.apiKey === 'string' && c.apiKey.trim()) ||
        (typeof c.adminApiKey === 'string' && c.adminApiKey.trim()),
      ) ||
      c.baseUrl.replace(/\/+$/, '') !== window.location.origin)
  )
}

export function needsStudioSetup(): boolean {
  try {
    // A new tab must reuse an existing connection, including pre-onboarding
    // installations. The session marker alone only survives a tab reload.
    if (
      sessionStorage.getItem(WORKSPACE_SESSION_KEY) !== 'selected' &&
      savedSetupConnections().length === 0
    )
      return true
    const saved = savedSetupConnections()
    const profiles = JSON.parse(
      readCredentialStorage(PROFILES_STORAGE_KEY) || 'null',
    ) as { activeId?: string } | null
    const active =
      saved.find((profile) => profile.id === profiles?.activeId) ?? saved.at(0)
    return !isStudioConnectionAllowed(
      active?.connection ?? { baseUrl: window.location.origin },
    )
  } catch {
    return true
  }
}

export function savedSetupConnections(): Array<{
  id: string
  name: string
  connection: ConnectionDraft
}> {
  try {
    const saved: unknown = JSON.parse(
      readCredentialStorage(PROFILES_STORAGE_KEY) || 'null',
    )
    if (
      saved &&
      typeof saved === 'object' &&
      'profiles' in saved &&
      Array.isArray(saved.profiles)
    )
      return saved.profiles.filter(
        (
          profile,
        ): profile is {
          id: string
          name: string
          connection: ConnectionDraft
        } =>
          profile &&
          typeof profile.id === 'string' &&
          typeof profile.name === 'string' &&
          hasConnection(profile.connection, true),
      )
    const legacy: unknown = JSON.parse(
      readCredentialStorage('ov_console_connection') || 'null',
    )
    if (
      hasConnection(
        legacy,
        localStorage.getItem(SETUP_STORAGE_KEY) === 'complete',
      )
    )
      return [
        {
          id: 'default',
          name: 'OpenViking',
          connection: normalizeProfileConnection(legacy as ConnectionDraft),
        },
      ]
  } catch {
    /* An invalid saved profile must not block a new connection. */
  }
  return []
}

export function markStudioSetupPending() {
  try {
    localStorage.setItem(SETUP_STORAGE_KEY, 'pending')
  } catch {
    /* Show the setup even without storage. */
  }
}

export async function completeStudioSetup(
  connection: ConnectionDraft,
  name: string,
  selectedProfileId?: string,
) {
  assertAllowedConnection(connection)
  // Do not enter the workspace if the browser cannot retain this connection.
  const saved = savedSetupConnections()
  const existing = selectedProfileId
    ? saved.find((profile) => profile.id === selectedProfileId)
    : saved.find(
        (profile) =>
          connectionProfileFingerprint(profile.connection) ===
          connectionProfileFingerprint(connection),
      )
  if (selectedProfileId && !existing)
    throw new Error(i18n.t('onboarding:savedConnectionUnavailable'))
  const id = existing?.id ?? createRandomUuid()
  writeCredentialStorage(
    PROFILES_STORAGE_KEY,
    JSON.stringify({
      activeId: id,
      profiles: existing
        ? saved.map((profile) =>
            profile.id === id ? { ...profile, connection } : profile,
          )
        : [...saved, { id, name, connection }],
    }),
  )
  writeCredentialStorage('ov_console_connection', JSON.stringify(connection))
  await flushCredentialStorage()
  localStorage.setItem(SETUP_STORAGE_KEY, 'complete')
  sessionStorage.setItem(WORKSPACE_SESSION_KEY, 'selected')
  return id
}
