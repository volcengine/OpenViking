/**
 * One deadline for every request a hook sends.
 *
 * A hook's requests run one after another — recall falls back through three
 * endpoints, capture sends then commits — so per-request timeouts alone add up
 * past the timeout the host gives the hook, and the host kills it mid-write.
 * Each request still gets its own timeout, or `defaultTimeoutMs`, but never
 * more than what is left of `budgetMs`.
 */
export function withRequestBudget(fetchJSON, budgetMs, defaultTimeoutMs = 0) {
  if (!budgetMs) return fetchJSON;
  const deadline = Date.now() + budgetMs;
  return (path, init = {}, options = {}) => {
    const remaining = deadline - Date.now();
    // ov-http deliberately clamps individual requests to one second. Do not
    // start one inside that final second or the host-level total can overrun.
    if (remaining < 1000) {
      return Promise.resolve({
        ok: false,
        status: 0,
        result: null,
        error: { name: "AbortError", aborted: true, message: "hook request budget exhausted" },
      });
    }
    const requested = Number(options.timeoutMs) || Number(defaultTimeoutMs) || remaining;
    return fetchJSON(path, init, { ...options, timeoutMs: Math.min(requested, remaining) });
  };
}
