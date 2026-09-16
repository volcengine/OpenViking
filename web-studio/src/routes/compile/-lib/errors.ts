const errorKeys = {
  INVALID_ARGUMENT: 'errors.invalidArgument',
  NOT_FOUND: 'errors.notFound',
  PERMISSION_DENIED: 'errors.permissionDenied',
  UNAUTHENTICATED: 'errors.unauthenticated',
  CONFLICT: 'errors.conflict',
  NETWORK_ERROR: 'errors.network',
  PAGINATION_UNSUPPORTED: 'errors.upgrade',
} as const

export function compileErrorKey(error: unknown) {
  const code =
    error && typeof error === 'object' && 'code' in error
      ? String(error.code)
      : ''
  return Object.hasOwn(errorKeys, code)
    ? errorKeys[code as keyof typeof errorKeys]
    : 'errors.generic'
}
