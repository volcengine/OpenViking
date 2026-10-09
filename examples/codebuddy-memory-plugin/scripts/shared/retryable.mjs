// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
export function isRetryableFailure(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  if (!status || status === 408 || status === 429 || status >= 500) {
    return true;
  }
  return status === 409 && result.error?.details?.retryable === true;
}
