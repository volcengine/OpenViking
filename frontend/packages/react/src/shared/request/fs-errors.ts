/** 请求被取消时不需要写回状态。A cancelled request has no state to write. */
export function isAbort(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}

/** 把任意抛出物归一成 Error。Normalises a thrown value into an Error. */
export const toError = (cause: unknown): Error =>
  cause instanceof Error ? cause : new Error(String(cause));
