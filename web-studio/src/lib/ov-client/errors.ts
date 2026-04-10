import axios, { AxiosError } from 'axios'

import type { OvClientErrorOptions, OvErrorEnvelope, OvResponse } from './types'

const MISSING_API_KEY_HINT = ' Please go to Settings and set X-API-Key.'
const MAX_ERROR_TEXT_LENGTH = 2000

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function truncateText(value: unknown, maxLength = MAX_ERROR_TEXT_LENGTH): string {
  const text = String(value || '')
  if (text.length <= maxLength) {
    return text
  }
  return `${text.slice(0, maxLength)}\n... (truncated, ${text.length} chars total)`
}

function withMissingApiKeyHint(code: string, message: string): string {
  if (code === 'UNAUTHENTICATED' && message.toLowerCase().includes('missing api key')) {
    return `${message}${MISSING_API_KEY_HINT}`
  }
  return message
}

function getEnvelope(value: unknown): OvErrorEnvelope | undefined {
  return isRecord(value) ? (value as OvErrorEnvelope) : undefined
}

function getRequestId(headers: unknown): string | undefined {
  if (!isRecord(headers)) {
    return undefined
  }

  const requestId = headers['x-request-id']
  return typeof requestId === 'string' ? requestId : undefined
}

function hasResult(value: unknown): value is { result: unknown } {
  return isRecord(value) && 'result' in value
}

function isResponseLike(value: unknown): value is OvResponse<unknown> {
  return (
    isRecord(value) &&
    'data' in value &&
    'headers' in value &&
    'status' in value
  )
}

export class OvClientError extends Error {
  code: string
  details?: unknown
  requestId?: string
  responseBody?: unknown
  statusCode?: number

  constructor(options: OvClientErrorOptions, cause?: unknown) {
    super(withMissingApiKeyHint(options.code, options.message), cause ? { cause } : undefined)
    this.name = 'OvClientError'
    this.code = options.code
    this.details = options.details
    this.requestId = options.requestId
    this.responseBody = options.responseBody
    this.statusCode = options.statusCode
  }
}

export function isOvClientError(error: unknown): error is OvClientError {
  return error instanceof OvClientError
}

export function normalizeOvClientError(error: unknown): OvClientError {
  if (error instanceof OvClientError) {
    return error
  }

  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<unknown>
    const envelope = getEnvelope(axiosError.response?.data)
    const statusCode = axiosError.response?.status
    const rawBody = axiosError.response?.data

    if (envelope?.error) {
      const normalized = new OvClientError(
        {
          code: envelope.error.code || 'ERROR',
          details: envelope.error.details ?? envelope.error.detail,
          message:
            envelope.error.message ||
            `Request failed with status ${statusCode ?? 'unknown'}`,
          requestId: getRequestId(axiosError.response?.headers),
          responseBody: rawBody,
          statusCode,
        },
        axiosError,
      )
      normalized.stack = axiosError.stack ?? normalized.stack
      return normalized
    }

    if (typeof rawBody === 'string' && rawBody.trim()) {
      const normalized = new OvClientError(
        {
          code: 'HTTP_ERROR',
          message: truncateText(rawBody),
          requestId: getRequestId(axiosError.response?.headers),
          responseBody: rawBody,
          statusCode,
        },
        axiosError,
      )
      normalized.stack = axiosError.stack ?? normalized.stack
      return normalized
    }

    const normalized = new OvClientError(
      {
        code: axiosError.code === AxiosError.ERR_NETWORK ? 'NETWORK_ERROR' : 'HTTP_ERROR',
        message:
          axiosError.message ||
          `Request failed with status ${statusCode ?? 'unknown'}`,
        requestId: getRequestId(axiosError.response?.headers),
        responseBody: rawBody,
        statusCode,
      },
      axiosError,
    )
    normalized.stack = axiosError.stack ?? normalized.stack
    return normalized
  }

  if (error instanceof Error) {
    const normalized = new OvClientError(
      {
        code: 'UNKNOWN_ERROR',
        message: error.message,
      },
      error,
    )
    normalized.stack = error.stack ?? normalized.stack
    return normalized
  }

  return new OvClientError({
    code: 'UNKNOWN_ERROR',
    message: String(error),
  })
}

export function unwrapOvResponse<TResult>(response: unknown): TResult {
  if (!isResponseLike(response)) {
    throw normalizeOvClientError(response)
  }

  const payload = response.data
  const envelope = getEnvelope(payload)

  if (envelope?.status === 'error') {
    throw new OvClientError({
      code: envelope.error.code || 'ERROR',
      details: envelope.error.details ?? envelope.error.detail,
      message: envelope.error.message || 'OpenViking request failed',
      requestId: getRequestId(response.headers),
      responseBody: payload,
      statusCode: response.status,
    })
  }

  if (hasResult(payload)) {
    return payload.result as TResult
  }

  return payload as TResult
}

export async function getOvResult<TResult>(request: Promise<unknown>): Promise<TResult> {
  try {
    return unwrapOvResponse(await request)
  } catch (error) {
    throw normalizeOvClientError(error)
  }
}