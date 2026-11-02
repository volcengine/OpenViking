import i18n from '#/i18n/core'
import { resolveApiKeyAuth } from '#/shared/api/auth'
import type { InternalAxiosRequestConfig } from 'axios'
import axios, { AxiosHeaders } from 'axios'

import { createClient } from '#/gen/ov-client/client'
import { client as sdkClient } from '#/gen/ov-client/client.gen'

import {
  normalizeOvClientError,
  OvClientError,
} from '#/shared/api/ov-client/errors'
import type {
  OvClientAdapter,
  OvClientOptions,
  OvConnectionState,
  OvErrorEnvelope,
} from '#/shared/api/ov-client/types'
import { DEFAULT_API_KEY_STORAGE_KEY } from '#/shared/api/ov-client/types'

const DEFAULT_TELEMETRY_PATHS = new Set([
  '/api/v1/search/find',
  '/api/v1/search/search',
  '/api/v1/resources',
])
const ADMIN_CONTROL_PLANE_PREFIXES = ['/api/v1/admin'] as const
const SESSION_COMMIT_PATH = /^\/api\/v1\/sessions\/[^/]+\/commit$/
function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

const ENV_BASE_URL =
  typeof import.meta.env.VITE_OV_BASE_URL === 'string'
    ? import.meta.env.VITE_OV_BASE_URL.trim()
    : ''

function normalizeBaseUrl(baseUrl?: string): string {
  const fallback = isBrowser() ? window.location.origin : ''
  return (baseUrl || ENV_BASE_URL || fallback).trim().replace(/\/+$/, '')
}

function readSessionStorage(key: string): string {
  if (!key || !isBrowser()) {
    return ''
  }

  try {
    return window.sessionStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeSessionStorage(key: string, value: string): void {
  if (!key || !isBrowser()) {
    return
  }

  try {
    if (value) {
      window.sessionStorage.setItem(key, value)
      return
    }
    window.sessionStorage.removeItem(key)
  } catch {
    // Ignore storage failures in restricted browser environments.
  }
}

function resolvePathname(rawUrl?: string): string {
  if (!rawUrl) {
    return ''
  }

  try {
    return new URL(rawUrl, 'http://openviking.local').pathname
  } catch {
    return rawUrl.startsWith('/') ? rawUrl : ''
  }
}

function readHeader(headers: unknown, name: string): string | undefined {
  if (headers instanceof AxiosHeaders) {
    const value = headers.get(name)
    return typeof value === 'string' ? value : undefined
  }

  if (isRecord(headers)) {
    const value = headers[name] ?? headers[name.toLowerCase()]
    return typeof value === 'string' ? value : undefined
  }

  return undefined
}

function setOptionalHeader(
  headers: AxiosHeaders,
  name: string,
  value: string,
): void {
  if (value.trim()) {
    headers.set(name, value)
    return
  }
  headers.delete(name)
}

function shouldInjectTelemetry(
  config: InternalAxiosRequestConfig,
  defaultTelemetry: boolean,
): boolean {
  if (!defaultTelemetry || config.method?.toUpperCase() !== 'POST') {
    return false
  }

  const pathname = resolvePathname(config.url)
  return (
    DEFAULT_TELEMETRY_PATHS.has(pathname) || SESSION_COMMIT_PATH.test(pathname)
  )
}

function shouldUseAdminApiKey(config: InternalAxiosRequestConfig): boolean {
  const pathname = resolvePathname(config.url)
  return ADMIN_CONTROL_PLANE_PREFIXES.some((prefix) =>
    pathname.startsWith(prefix),
  )
}

// Root credentials may serve read-only operational endpoints, never tenant data.
function canUseControlCredential(config: InternalAxiosRequestConfig): boolean {
  if (config.method?.toUpperCase() !== 'GET') return false
  const path = resolvePathname(config.url)
  return (
    ['/health', '/api/v1/system/status', '/api/v1/debug/health'].includes(
      path,
    ) ||
    ['/api/v1/console/', '/api/v1/observer/'].some((prefix) =>
      path.startsWith(prefix),
    ) ||
    /^\/api\/v1\/tasks(?:\/|$)/.test(path)
  )
}

function maybeInjectTelemetry(
  config: InternalAxiosRequestConfig,
  defaultTelemetry: boolean,
): void {
  if (!shouldInjectTelemetry(config, defaultTelemetry)) {
    return
  }

  if (!isRecord(config.data) || config.data.telemetry !== undefined) {
    return
  }

  config.data = {
    ...config.data,
    telemetry: true,
  }
}

function isEnvelopeError(value: unknown): value is OvErrorEnvelope & {
  error: NonNullable<OvErrorEnvelope['error']>
  status: 'error'
} {
  return isRecord(value) && value.status === 'error' && isRecord(value.error)
}

export function createOvClient(options: OvClientOptions = {}): OvClientAdapter {
  const bindSdkClient = options.bindSdkClient ?? false
  let runtimeOptions = {
    credentialScope: options.credentialScope ?? 'auto',
    apiKeyAuth:
      options.apiKeyAuth ??
      resolveApiKeyAuth(normalizeBaseUrl(options.baseUrl)),
    apiKeyStorageKey: options.apiKeyStorageKey ?? DEFAULT_API_KEY_STORAGE_KEY,
    baseUrl: normalizeBaseUrl(options.baseUrl),
    defaultTelemetry: options.defaultTelemetry ?? true,
  }

  let connection: OvConnectionState = {
    adminApiKey: options.connection?.adminApiKey ?? '',
    apiKey:
      options.connection?.apiKey ??
      readSessionStorage(runtimeOptions.apiKeyStorageKey),
    accountId: options.connection?.accountId ?? '',
    identityHeaders: options.connection?.identityHeaders ?? false,
    trustedAuth: options.connection?.trustedAuth ?? false,
    userId: options.connection?.userId ?? '',
  }

  const instance = options.axios ?? axios.create()
  const defaultHeaders = { ...(options.defaultHeaders || {}) }
  const client = createClient({
    axios: instance,
    baseURL: runtimeOptions.baseUrl,
    headers: defaultHeaders,
    throwOnError: true,
  })

  instance.interceptors.request.use((config) => {
    const headers = AxiosHeaders.from(config.headers)

    for (const [key, value] of Object.entries(defaultHeaders)) {
      headers.set(key, value)
    }

    if (
      runtimeOptions.credentialScope === 'data' &&
      shouldUseAdminApiKey(config)
    ) {
      throw new OvClientError({
        code: 'PERMISSION_DENIED',
        statusCode: 403,
        message: i18n.t('studio:states.managementRequired.title'),
      })
    }

    // Workbench callers cannot override the data credential with a control
    // header left over from management mode.
    if (runtimeOptions.credentialScope === 'data') {
      headers.delete('X-API-Key')
      headers.delete('Authorization')
    }
    const credentialHeader =
      runtimeOptions.apiKeyAuth === 'bearer' ? 'Authorization' : 'X-API-Key'
    if (!readHeader(headers, credentialHeader)?.trim()) {
      const apiKey =
        runtimeOptions.apiKeyAuth === 'bearer' ||
        runtimeOptions.credentialScope === 'data'
          ? connection.trustedAuth
            ? connection.adminApiKey
            : connection.apiKey
          : shouldUseAdminApiKey(config)
            ? connection.adminApiKey || connection.apiKey
            : connection.apiKey ||
              (connection.identityHeaders || canUseControlCredential(config)
                ? connection.adminApiKey
                : '')
      setOptionalHeader(
        headers,
        credentialHeader,
        apiKey && runtimeOptions.apiKeyAuth === 'bearer'
          ? `Bearer ${apiKey}`
          : apiKey,
      )
    }
    if (
      connection.identityHeaders &&
      !(connection.trustedAuth && shouldUseAdminApiKey(config))
    ) {
      if (!readHeader(headers, 'X-OpenViking-Account')?.trim()) {
        setOptionalHeader(headers, 'X-OpenViking-Account', connection.accountId)
      }
      if (!readHeader(headers, 'X-OpenViking-User')?.trim()) {
        setOptionalHeader(headers, 'X-OpenViking-User', connection.userId)
      }
    } else {
      headers.delete('X-OpenViking-Account')
      headers.delete('X-OpenViking-User')
    }

    config.headers = headers
    maybeInjectTelemetry(config, runtimeOptions.defaultTelemetry)

    return config
  })

  instance.interceptors.response.use(
    (response) => {
      const requestId = readHeader(response.headers, 'x-request-id')

      if (isEnvelopeError(response.data)) {
        const { error } = response.data
        const message = error.message || i18n.t('studio:errors.requestFailed')

        throw new OvClientError({
          code: error.code || 'ERROR',
          details: error.details ?? error.detail,
          message,
          requestId,
          responseBody: response.data,
          statusCode: response.status,
        })
      }

      return response
    },
    (error) => {
      const normalized = normalizeOvClientError(error)
      return Promise.reject(normalized)
    },
  )

  function persistApiKey(): void {
    writeSessionStorage(runtimeOptions.apiKeyStorageKey, connection.apiKey)
  }

  function syncClientConfig(): void {
    client.setConfig({
      baseURL: runtimeOptions.baseUrl,
      throwOnError: true,
    })

    if (!bindSdkClient) {
      return
    }

    sdkClient.setConfig({
      axios: instance,
      baseURL: runtimeOptions.baseUrl,
      headers: defaultHeaders,
      throwOnError: true,
    })
  }

  function getConnection(): Readonly<OvConnectionState> {
    return { ...connection }
  }

  function setConnection(next: Partial<OvConnectionState>): OvConnectionState {
    connection = {
      ...connection,
      ...next,
    }
    persistApiKey()
    return { ...connection }
  }

  function clearConnection(): OvConnectionState {
    connection = {
      adminApiKey: '',
      apiKey: '',
      accountId: '',
      identityHeaders: false,
      trustedAuth: false,
      userId: '',
    }
    persistApiKey()
    return { ...connection }
  }

  function getOptions(): Readonly<typeof runtimeOptions> {
    return { ...runtimeOptions }
  }

  function setOptions(
    next: Partial<typeof runtimeOptions>,
  ): Readonly<typeof runtimeOptions> {
    const previousStorageKey = runtimeOptions.apiKeyStorageKey
    runtimeOptions = {
      credentialScope: next.credentialScope ?? runtimeOptions.credentialScope,
      apiKeyAuth:
        next.apiKeyAuth ??
        (next.baseUrl !== undefined
          ? resolveApiKeyAuth(normalizeBaseUrl(next.baseUrl))
          : runtimeOptions.apiKeyAuth),
      apiKeyStorageKey:
        next.apiKeyStorageKey ?? runtimeOptions.apiKeyStorageKey,
      baseUrl:
        next.baseUrl !== undefined
          ? normalizeBaseUrl(next.baseUrl)
          : runtimeOptions.baseUrl,
      defaultTelemetry:
        next.defaultTelemetry ?? runtimeOptions.defaultTelemetry,
    }

    if (previousStorageKey !== runtimeOptions.apiKeyStorageKey) {
      writeSessionStorage(previousStorageKey, '')
    }
    persistApiKey()
    syncClientConfig()
    return { ...runtimeOptions }
  }

  persistApiKey()
  syncClientConfig()

  return {
    clearConnection,
    client,
    getConnection,
    getOptions,
    instance,
    setConnection,
    setOptions,
  }
}

export const ovClient = createOvClient({
  credentialScope: 'data',
  apiKeyStorageKey: '',
  baseUrl: ENV_BASE_URL,
  bindSdkClient: true,
})
