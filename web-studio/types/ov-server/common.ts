export type JsonObject = Record<string, unknown>

export type OvResponseStatus = 'success' | 'error' | (string & {})

export type OvEnvelope<TResult = unknown> = {
  result?: TResult
  status?: OvResponseStatus
  telemetry?: unknown
}

export type OvErrorEnvelope = {
  error?: {
    code?: string
    detail?: unknown
    details?: unknown
    message?: string
  }
  status?: OvResponseStatus
}

export type OvDisabledResult = {
  enabled: false
  message?: string
}

export type OvMaybeDisabled = {
  enabled?: boolean
  message?: string
}

export type PaginatedResult<TItem> = {
  items?: TItem[]
  page?: number
  page_size?: number
  total?: number
}
