// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * A budget-exhausted request, not a server failure.
 *
 * The hook ran out of time before it could ask. Nothing reached the server, so
 * this must neither burn a retry nor be recorded as a permanent failure: the
 * next boundary will try again with a fresh budget.
 */
export function isDeferredFailure(result) {
  return Boolean(result && !result.ok && result.error?.deferred === true);
}

export function isRetryableFailure(result) {
  if (!result || result.ok) return false;
  if (isDeferredFailure(result)) return false;
  const status = Number(result.status || 0);
  if (!status || status === 408 || status === 429 || status >= 500) {
    return true;
  }
  return status === 409 && result.error?.details?.retryable === true;
}
