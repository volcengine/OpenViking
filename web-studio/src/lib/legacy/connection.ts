import { ovClient } from '#/lib/ov-client'

export type LegacyConnectionSettings = {
  accountId: string
  agentId: string
  apiKey: string
  baseUrl: string
  userId: string
}

const LEGACY_SETTINGS_STORAGE_KEY = 'ov_console_legacy_connection_settings_v2'
const LEGACY_SESSION_API_KEY = 'ov_console_api_key'

function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

function normalizeValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function getDefaultBaseUrl(): string {
  return ovClient.getOptions().baseUrl || (isBrowser() ? window.location.origin : '')
}

export function loadLegacyConnectionSettings(): LegacyConnectionSettings {
  if (!isBrowser()) {
    return {
      accountId: '',
      agentId: '',
      apiKey: '',
      baseUrl: getDefaultBaseUrl(),
      userId: '',
    }
  }

  let stored: Record<string, unknown> = {}
  try {
    const raw = window.localStorage.getItem(LEGACY_SETTINGS_STORAGE_KEY)
    stored = raw ? (JSON.parse(raw) as Record<string, unknown>) : {}
  } catch {
    stored = {}
  }

  return {
    accountId: normalizeValue(stored.accountId),
    agentId: normalizeValue(stored.agentId),
    apiKey: normalizeValue(window.sessionStorage.getItem(LEGACY_SESSION_API_KEY) || ''),
    baseUrl: normalizeValue(stored.baseUrl) || getDefaultBaseUrl(),
    userId: normalizeValue(stored.userId),
  }
}

export function persistLegacyConnectionSettings(
  settings: LegacyConnectionSettings,
): LegacyConnectionSettings {
  if (!isBrowser()) {
    return settings
  }

  window.localStorage.setItem(
    LEGACY_SETTINGS_STORAGE_KEY,
    JSON.stringify({
      accountId: settings.accountId,
      agentId: settings.agentId,
      baseUrl: settings.baseUrl,
      userId: settings.userId,
    }),
  )

  return settings
}

export function applyLegacyConnectionSettings(settings: LegacyConnectionSettings): void {
  ovClient.setOptions({ baseUrl: settings.baseUrl })
  ovClient.setConnection({
    accountId: settings.accountId,
    agentId: settings.agentId,
    apiKey: settings.apiKey,
    userId: settings.userId,
  })
}

export function clearLegacyConnectionSettings(): LegacyConnectionSettings {
  if (isBrowser()) {
    window.localStorage.removeItem(LEGACY_SETTINGS_STORAGE_KEY)
  }

  ovClient.clearConnection()

  return {
    accountId: '',
    agentId: '',
    apiKey: '',
    baseUrl: getDefaultBaseUrl(),
    userId: '',
  }
}
