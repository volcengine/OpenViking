/**
 * The one path from a hook to the OpenViking server.
 *
 * Every harness used to own its own AbortController, header block and envelope
 * parser, and they drifted: one sent the api key twice, one named the operator
 * to every proxy on the path whether or not the server was trusted with it, one
 * read a different field for the same account. A server-side change now lands
 * in one place instead of eight.
 *
 * `X-API-Key` is deliberately absent. The server takes `Authorization: Bearer`,
 * and the identity headers go out only when the resolved config says this
 * server is trusted with the operator's name.
 */

// A request that aborts the instant it starts reads as a dead server. A config
// that asks for less than a second gets a second.
const MIN_TIMEOUT_MS = 1000;

/**
 * The headers every harness puts on every OpenViking request.
 *
 * `identityHeaders` overrides `cfg.sendIdentityHeaders` for the callers that
 * decide per request rather than per config — the doctor probes both ways.
 */
export function buildOvHeaders(cfg = {}, { actorPeerId = "", identityHeaders, extraHeaders } = {}) {
  const identity = identityHeaders === undefined ? Boolean(cfg.sendIdentityHeaders) : Boolean(identityHeaders);
  const headers = { "Content-Type": "application/json" };
  if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
  if (identity && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
  if (identity && cfg.user) headers["X-OpenViking-User"] = cfg.user;
  if (actorPeerId) headers["X-OpenViking-Actor-Peer"] = actorPeerId;
  if (cfg.userAgent) headers["User-Agent"] = cfg.userAgent;
  return { ...headers, ...extraHeaders };
}

function responseTraceId(body) {
  return body?.result?.trace_id || body?.error?.trace_id || body?.trace_id || undefined;
}

/**
 * Build the `fetchJSON(path, init, { timeoutMs, actorPeerId })` a harness talks
 * through. It resolves to `{ ok, status, result, error, traceId }` and never
 * throws: a hook that crashes on a closed port would take its host's turn with
 * it, so a network error is `{ ok: false, status: 0 }` and the retry rules in
 * `retryable.mjs` decide what happens next.
 *
 * `resolveActorPeerId` is a callback because Codex only knows its peer after
 * loading session state under the lock, well after the client is built.
 * `requireJsonBody` is for the capture hooks, which would rather retry a body
 * they cannot parse than record the turn as sent.
 */
export function createOvHttp(cfg = {}, {
  defaultTimeoutMs = 0,
  resolveActorPeerId = () => "",
  identityHeaders,
  extraHeaders,
  requireJsonBody = false,
} = {}) {
  // dsh and pi name this field `endpoint`; every other harness names it `baseUrl`.
  const baseUrl = cfg.baseUrl || cfg.endpoint || "";
  return async function fetchJSON(path, init = {}, options = {}) {
    const timeoutMs = Math.max(MIN_TIMEOUT_MS, Number(options.timeoutMs) || Number(defaultTimeoutMs) || 0);
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      const headers = {
        ...buildOvHeaders(cfg, {
          actorPeerId: options.actorPeerId ?? resolveActorPeerId(),
          identityHeaders,
          extraHeaders,
        }),
        ...(init.headers || {}),
      };
      const response = await fetch(`${baseUrl}${path}`, { ...init, headers, signal: controller.signal });
      const body = await response.json().catch(() => null);
      if (requireJsonBody && !body) {
        return {
          ok: false,
          status: response.status,
          result: null,
          error: { message: "empty or invalid JSON response" },
        };
      }
      const traceId = responseTraceId(body);
      if (!response.ok || body?.status === "error") {
        return {
          ok: false,
          status: response.status,
          result: null,
          error: body?.error || { message: `HTTP ${response.status}` },
          traceId,
        };
      }
      return { ok: true, status: response.status, result: body?.result ?? body ?? {}, traceId };
    } catch (error) {
      const message = error?.message || String(error);
      // This module owns the controller, so it says a timeout was a timeout
      // rather than leaving callers to guess it from whatever fetch threw.
      const failure = timedOut
        ? { name: "AbortError", aborted: true, message }
        : { message };
      return { ok: false, status: 0, result: null, error: failure };
    } finally {
      clearTimeout(timer);
    }
  };
}
