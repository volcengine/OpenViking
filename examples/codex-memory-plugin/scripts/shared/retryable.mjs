// GENERATED FROM examples/agent-hook-plugin/lib. DO NOT EDIT.
export function isRetryableFailure(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  if (!status || status === 408 || status === 429 || status >= 500) {
    return true;
  }
  return status === 409 && result.error?.details?.retryable === true;
}

/** The server refused the credentials: nothing will land until they are fixed. */
export function isAuthFailure(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  return status === 401 || status === 403;
}
