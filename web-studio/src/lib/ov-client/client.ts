import axios, { AxiosHeaders } from 'axios'
import type { InternalAxiosRequestConfig } from 'axios'

import { createClient } from '#/gen/ov-client/client'
import { client as sdkClient } from '#/gen/ov-client/client.gen'

import { normalizeOvClientError, OvClientError } from './errors'
import { addRequestLog, updateRequestLog } from '../request-logs'
import { DEFAULT_API_KEY_STORAGE_KEY } from './types'
import type { OvClientAdapter, OvClientOptions, OvConnectionState, OvErrorEnvelope } from './types'

const DEFAULT_TELEMETRY_PATHS = new Set(['/api/v1/search/find', '/api/v1/resources'])
const SESSION_COMMIT_PATH = /^\/api\/v1\/sessions\/[^/]+\/commit$/
const WEB_STUDIO_AGENT_ID = 'web-studio'
const REQUEST_LOG_META_KEY = '__ovRequestLog'

type RequestLogMeta = {
  id: string
  startedAt: number
}

declare module 'axios' {
  export interface InternalAxiosRequestConfig {
    [REQUEST_LOG_META_KEY]?: RequestLogMeta
  }
}

function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

const ENV_BASE_URL = typeof import.meta.env.VITE_OV_BASE_URL === 'string'
  ? import.meta.env.VITE_OV_BASE_URL.trim()
  : ''

function normalizeBaseUrl(baseUrl?: string): string {
  const fallback = isBrowser() ? window.location.origin : ''
  return (baseUrl || ENV_BASE_URL || fallback).trim().replace(/\/+$/, '')
}

function readSessionStorage(key: string): string {
  if (!isBrowser()) {
    return ''
  }

  try {
    return window.sessionStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeSessionStorage(key: string, value: string): void {
  if (!isBrowser()) {
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

function resolveRequestPath(rawUrl?: string, params?: unknown): string {
  if (!rawUrl) {
    return ''
  }

  try {
    const url = new URL(rawUrl, 'http://openviking.local')
    const query = url.search || (params ? '?...' : '')
    return `${url.pathname}${query}`
  } catch {
    return rawUrl
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

function setOptionalHeader(headers: AxiosHeaders, name: string, value: string): void {
  if (value.trim()) {
    headers.set(name, value)
    return
  }
  headers.delete(name)
}

function shouldInjectTelemetry(config: InternalAxiosRequestConfig, defaultTelemetry: boolean): boolean {
  if (!defaultTelemetry || config.method?.toUpperCase() !== 'POST') {
    return false
  }

  const pathname = resolvePathname(config.url)
  return DEFAULT_TELEMETRY_PATHS.has(pathname) || SESSION_COMMIT_PATH.test(pathname)
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

function isEnvelopeError(
  value: unknown,
): value is OvErrorEnvelope & { error: NonNullable<OvErrorEnvelope['error']>; status: 'error' } {
  return isRecord(value) && value.status === 'error' && isRecord(value.error)
}

export function createOvClient(options: OvClientOptions = {}): OvClientAdapter {
  const bindSdkClient = options.bindSdkClient ?? false
  let runtimeOptions = {
    apiKeyStorageKey: options.apiKeyStorageKey || DEFAULT_API_KEY_STORAGE_KEY,
    baseUrl: normalizeBaseUrl(options.baseUrl),
    defaultTelemetry: options.defaultTelemetry ?? true,
  }

  let connection: OvConnectionState = {
    apiKey: options.connection?.apiKey ?? readSessionStorage(runtimeOptions.apiKeyStorageKey),
    accountId: options.connection?.accountId ?? '',
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

    setOptionalHeader(headers, 'X-API-Key', connection.apiKey)
    setOptionalHeader(headers, 'X-OpenViking-Account', connection.accountId)
    setOptionalHeader(headers, 'X-OpenViking-User', connection.userId)
    headers.set('X-OpenViking-Agent', WEB_STUDIO_AGENT_ID)

    config.headers = headers
    maybeInjectTelemetry(config, runtimeOptions.defaultTelemetry)

    const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    const startedAt = Date.now()
    config[REQUEST_LOG_META_KEY] = { id, startedAt }
    addRequestLog({
      id,
      method: (config.method || 'GET').toUpperCase(),
      path: resolveRequestPath(config.url, config.params),
      startedAt: new Date(startedAt).toISOString(),
      status: 'pending',
    })

    return config
  })

  instance.interceptors.response.use(
    (response) => {
      const meta = response.config[REQUEST_LOG_META_KEY]
      const requestId = readHeader(response.headers, 'x-request-id')

      if (isEnvelopeError(response.data)) {
        const { error } = response.data
        const message = error.message || 'OpenViking request failed'

        if (meta) {
          updateRequestLog(meta.id, {
            durationMs: Date.now() - meta.startedAt,
            errorMessage: message,
            requestId,
            status: 'error',
            statusCode: response.status,
          })
        }

        throw new OvClientError({
          code: error.code || 'ERROR',
          details: error.details ?? error.detail,
          message,
          requestId,
          responseBody: response.data,
          statusCode: response.status,
        })
      }

      if (meta) {
        updateRequestLog(meta.id, {
          durationMs: Date.now() - meta.startedAt,
          requestId,
          status: 'success',
          statusCode: response.status,
        })
      }

      return response
    },
    (error) => {
      const normalized = normalizeOvClientError(error)
      const meta = isRecord(error) && isRecord(error.config)
        ? (error.config[REQUEST_LOG_META_KEY] as RequestLogMeta | undefined)
        : undefined
      if (meta) {
        updateRequestLog(meta.id, {
          durationMs: Date.now() - meta.startedAt,
          errorMessage: normalized.message,
          requestId: normalized.requestId,
          status: 'error',
          statusCode: normalized.statusCode,
        })
      }

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
      apiKey: '',
      accountId: '',
      userId: '',
    }
    persistApiKey()
    return { ...connection }
  }

  function getOptions(): Readonly<typeof runtimeOptions> {
    return { ...runtimeOptions }
  }

  function setOptions(next: Partial<typeof runtimeOptions>): Readonly<typeof runtimeOptions> {
    const previousStorageKey = runtimeOptions.apiKeyStorageKey
    runtimeOptions = {
      apiKeyStorageKey: next.apiKeyStorageKey ?? runtimeOptions.apiKeyStorageKey,
      baseUrl: next.baseUrl !== undefined ? normalizeBaseUrl(next.baseUrl) : runtimeOptions.baseUrl,
      defaultTelemetry: next.defaultTelemetry ?? runtimeOptions.defaultTelemetry,
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

const DEFAULT_LOCAL_BASE_URL = 'http://127.0.0.1:1933'

export const ovClient = createOvClient({
  baseUrl: ENV_BASE_URL || DEFAULT_LOCAL_BASE_URL,
  bindSdkClient: true,
})
