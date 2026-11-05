import { buildOvHeaders } from "./shared/ov-http.mjs";

// These restrictions belong to this gateway's file-only connection policy.
const RESERVED = new Set(["content-type", "authorization", "x-api-key", "host", "content-length", "connection", "transfer-encoding"]);

export function connectionHeaders(cfg) {
  const extras = Object.fromEntries(Object.entries(cfg.extraHeaders || {}).filter(([name]) => {
    const key = name.toLowerCase();
    return !RESERVED.has(key) && !key.startsWith("x-openviking-");
  }));
  if (cfg.authorization) extras.Authorization = cfg.authorization;
  return buildOvHeaders(cfg, { actorPeerId: cfg.peerId || "", extraHeaders: extras });
}

export class PluginError extends Error {
  constructor(code, status = 0, traceId) {
    super(`OpenViking: ${code}${status ? ` (HTTP ${status})` : ""}`);
    this.code = code;
    this.status = status;
    this.traceId = traceId;
  }
}

/** Cancellable requests fit Magpie's hook budget and expose only safe errors. */
export async function request(cfg, path, { body, signal, timeoutMs = cfg.timeoutMs } = {}) {
  const timeout = AbortSignal.timeout(Math.max(100, timeoutMs));
  const abort = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    const response = await fetch(`${cfg.baseUrl}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: connectionHeaders(cfg),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: abort, redirect: "error",
    });
    const result = await response.json().catch((error) => {
      if (!(error instanceof SyntaxError)) throw error;
      return null;
    });
    const traceId = result?.result?.trace_id || result?.error?.trace_id || result?.trace_id;
    if (!response.ok) {
      const code = ({ 400: "invalid", 401: "unauthorized", 403: "unauthorized", 404: "not_found",
        408: "timeout", 409: "conflict", 422: "invalid", 429: "rate_limited" })[response.status]
        || (response.status >= 500 ? "unavailable" : "http_error");
      throw new PluginError(code, response.status, traceId);
    }
    if (!result || typeof result !== "object" || result.status !== "ok" || !("result" in result)) {
      throw new PluginError("invalid", response.status, traceId);
    }
    return result.result;
  } catch (error) {
    if (signal?.aborted) throw new PluginError("cancelled");
    if (timeout.aborted) throw new PluginError("timeout");
    if (error instanceof PluginError) throw error;
    throw new PluginError("unavailable");
  }
}
