import type { CapabilityState } from '#/platform/access/features'

export function classifyCapabilityError(error: {
  code?: string
  statusCode?: number
  responseBody?: unknown
}): CapabilityState {
  const body = error.responseBody as
    | { ResponseMetadata?: { Error?: { Code?: string } } }
    | undefined
  if (
    error.code === 'ApiBlocked' ||
    body?.ResponseMetadata?.Error?.Code === 'ApiBlocked'
  )
    return 'unavailable'
  if (error.statusCode === 403) return 'forbidden'
  // Only used for object-free endpoint probes, never file/stat requests.
  if (error.statusCode === 404 || error.statusCode === 405) return 'unavailable'
  return 'unknown'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

// A reachable endpoint is not enough: verify the read contract used by its page.
export function classifyCapabilityResponse(
  featureId: string,
  payload: unknown,
): CapabilityState {
  if (!isRecord(payload)) return 'unknown'
  const metadata = payload.ResponseMetadata
  if (
    payload.status === 'error' ||
    payload.error ||
    (isRecord(metadata) && metadata.Error)
  ) {
    const error = isRecord(payload.error) ? payload.error : undefined
    return classifyCapabilityError({
      code: typeof error?.code === 'string' ? error.code : undefined,
      responseBody: payload,
    })
  }
  const result = 'result' in payload ? payload.result : payload
  if (!isRecord(result)) return 'unknown'
  if (result.enabled === false) return 'unavailable'
  switch (featureId) {
    case 'watches':
      return Array.isArray(result.tasks) ? 'supported' : 'unknown'
    case 'requestLogs':
      return Array.isArray(result.items) && typeof result.total === 'number'
        ? 'supported'
        : 'unknown'
    case 'monitoring':
      return typeof result.is_healthy === 'boolean' &&
        isRecord(result.components)
        ? 'supported'
        : 'unknown'
    default:
      return 'unknown'
  }
}
