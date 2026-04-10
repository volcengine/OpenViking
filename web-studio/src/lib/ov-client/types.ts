import type { AxiosInstance, AxiosResponse } from 'axios'

import type { Client } from '#/gen/ov-client/client'

export const DEFAULT_API_KEY_STORAGE_KEY = 'ov_console_api_key'

export interface OvConnectionState {
  apiKey: string
  accountId: string
  userId: string
}

export interface OvClientOptions {
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
  getOptions: () => Readonly<Required<Pick<OvClientOptions, 'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry'>>>
  setConnection: (next: Partial<OvConnectionState>) => OvConnectionState
  setOptions: (
    next: Partial<Pick<OvClientOptions, 'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry'>>,
  ) => Readonly<Required<Pick<OvClientOptions, 'apiKeyStorageKey' | 'baseUrl' | 'defaultTelemetry'>>>
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