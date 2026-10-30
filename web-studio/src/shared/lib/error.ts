import i18n from '#/i18n/core'

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// Diagnostics belong in details; the primary message follows the UI language.
export function getLocalizedErrorMessage(
  error: unknown,
  fallback?: string,
): string {
  const record =
    typeof error === 'object' && error !== null
      ? (error as {
          statusCode?: number
          code?: string
          response?: { status?: number }
        })
      : undefined
  const status = record?.statusCode ?? record?.response?.status
  const code = record?.code
  if (status === 401 || code === 'UNAUTHENTICATED')
    return i18n.t('studio:errors.authenticationFailed')
  if (status === 403 || code === 'PERMISSION_DENIED' || code === 'FORBIDDEN')
    return i18n.t('studio:errors.permissionDenied')
  if (status === 404 || code === 'NOT_FOUND')
    return i18n.t('studio:errors.resourceNotFound')
  if (status === 409 || code === 'ALREADY_EXISTS' || code === 'CONFLICT')
    return i18n.t('studio:errors.requestConflict')
  if (status === 413) return i18n.t('studio:errors.payloadTooLarge')
  if (status === 429 || code === 'RATE_LIMITED')
    return i18n.t('studio:errors.rateLimited')
  if (
    status === 408 ||
    status === 504 ||
    code === 'ECONNABORTED' ||
    code === 'ETIMEDOUT'
  )
    return i18n.t('studio:errors.requestTimedOut')
  if (code === 'ERR_NETWORK' || code === 'NETWORK_ERROR')
    return i18n.t('studio:errors.networkFailed')
  if (status !== undefined && status >= 500)
    return i18n.t('studio:errors.serviceUnavailable')
  if (status === 400 || status === 422 || code === 'INVALID_ARGUMENT')
    return i18n.t('studio:errors.requestInvalid')
  return fallback || i18n.t('studio:errors.requestFailed')
}
