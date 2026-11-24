import { isOvClientError } from '#/shared/api/ov-client/index'

const errorKeys = {
  INVALID_URI: 'errors.invalidArgument',
  INVALID_ARGUMENT: 'errors.invalidArgument',
  NOT_FOUND: 'errors.notFound',
  PERMISSION_DENIED: 'errors.permissionDenied',
  UNAUTHENTICATED: 'errors.unauthenticated',
  CONFLICT: 'errors.conflict',
  NETWORK_ERROR: 'errors.network',
  PAGINATION_UNSUPPORTED: 'errors.upgrade',
} as const

export function compileErrorKey(error: unknown) {
  if (
    isOvClientError(error) &&
    error.details &&
    typeof error.details === 'object' &&
    'reason' in error.details
  ) {
    const reason = error.details.reason
    if (reason === 'COMPILE_SCHEDULER_DISABLED')
      return 'schedule.schedulerDisabled'
    if (reason === 'COMPILE_CREDENTIAL_INVALID')
      return 'schedule.credentialInvalid'
    if (reason === 'COMPILE_CREDENTIAL_KEY_UNAVAILABLE')
      return 'schedule.credentialKeyUnavailable'
  }
  const code =
    error && typeof error === 'object' && 'code' in error
      ? String(error.code)
      : ''
  return Object.hasOwn(errorKeys, code)
    ? errorKeys[code as keyof typeof errorKeys]
    : 'errors.generic'
}

/** These responses confirm that this request was rejected before creation. */
export function isRejectedCompileSubmission(error: unknown): boolean {
  return (
    isOvClientError(error) &&
    [
      'INVALID_ARGUMENT',
      'INVALID_URI',
      'NOT_FOUND',
      'PERMISSION_DENIED',
      'UNAUTHENTICATED',
      'CONFLICT',
    ].includes(error.code)
  )
}
