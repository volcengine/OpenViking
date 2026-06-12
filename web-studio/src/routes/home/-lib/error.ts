import { isOvClientError } from '#/lib/ov-client/errors'

/**
 * Resolve a human-readable message for a failed dashboard query.
 *
 * Console queries reject with an `OvClientError` whose `message` already carries
 * the server-provided detail (status code, envelope message, missing api-key
 * hint, ...). Surfacing it lets self-hosted operators tell an auth failure from
 * a 5xx or a network error instead of seeing an opaque literal. Returns
 * `undefined` for non-errors or blank messages so callers fall back to a
 * generic label.
 */
export function resolveQueryErrorMessage(error: unknown): string | undefined {
  if (!isOvClientError(error) && !(error instanceof Error)) {
    return undefined
  }
  const message = error.message.trim()
  return message.length > 0 ? message : undefined
}
