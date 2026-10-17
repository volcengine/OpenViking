/**
 * One wall clock shared by every request a short-lived hook makes. The host
 * kills the process at its manifest timeout (30s for SessionStart), so an
 * unbounded sequence of 15s requests can die mid-replay and leave a claimed
 * queue entry stuck until the ten-minute stale sweep.
 */

export function createRemainingMs(budgetMs, now = Date.now) {
  const deadline = now() + budgetMs;
  return () => Math.max(0, deadline - now());
}

export function boundFetch(fetchJSON, remainingMs, defaultTimeoutMs) {
  return (path, init = {}, options = {}) => {
    const remaining = remainingMs();
    if (remaining <= 0) {
      return Promise.resolve({ ok: false, status: 0, error: "hook budget exhausted" });
    }
    const requested = Number(options.timeoutMs) || Number(defaultTimeoutMs) || remaining;
    return fetchJSON(path, init, { ...options, timeoutMs: Math.min(requested, remaining) });
  };
}
