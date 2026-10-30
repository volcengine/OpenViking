export function isRetryableFailure(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  if (status === 429) {
    return result.error?.details?.retryable !== false;
  }
  if (!status || status === 408 || status >= 500) {
    return true;
  }
  return status === 409 && result.error?.details?.retryable === true;
}
