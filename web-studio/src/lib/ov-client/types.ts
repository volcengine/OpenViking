import type { AxiosInstance, AxiosResponse } from 'axios'

import type { Client } from '#/gen/ov-client/client'
import type { ApiKeyAuth } from '#/lib/connection/auth'

export const DEFAULT_API_KEY_STORAGE_KEY = 'ov_console_api_key'

export interface OvConnectionState {
  adminApiKey: string
  apiKey: string
  accountId: string
  identityHeaders: boolean
  userId: string
}

export interface OvClientOptions {
  apiKeyAuth?: ApiKeyAuth
  apiKeyStorageKey?: string
  axios?: AxiosInstance
  baseUrl?: string
  bindSdkClient?: boolean
  connection?: Partial<OvConnectionState>
  defaultHeaders?: Record<string, string>
  defaultTelemetry?: boolean
}

export interface OvClientAdapter {
  client: Client
  instance: AxiosInstance
  clearConnection: () => OvConnectionState
  getConnection: () => Readonly<OvConnectionState>
  getOptions: () => Readonly<
    Required<
      Pick<
        OvClientOptions,
        'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry' | 'apiKeyAuth'
      >
    >
  >
  setConnection: (next: Partial<OvConnectionState>) => OvConnectionState
  setOptions: (
    next: Partial<
      Pick<
        OvClientOptions,
        'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry' | 'apiKeyAuth'
      >
    >,
  ) => Readonly<
    Required<
      Pick<
        OvClientOptions,
        'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry' | 'apiKeyAuth'
      >
    >
  >
}

export interface OvErrorEnvelope {
  status?: string
  error?: {
    code?: string
    detail?: unknown
    details?: unknown
    message?: string
  }
}

export interface OvClientErrorOptions {
  code: string
  details?: unknown
  message: string
  requestId?: string
  responseBody?: unknown
  statusCode?: number
}

export interface OvSuccessEnvelope<TResult = unknown> {
  result?: TResult
  status?: string
  telemetry?: unknown
}

export type OvResponse<TResult = unknown> = AxiosResponse<unknown> & {
  data: OvSuccessEnvelope<TResult> | TResult | unknown
}
