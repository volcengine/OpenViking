/**
 * Persistent OpenViking session helpers for Claude Code hooks.
 *
 * ovSessionId is deterministically derived from the CC session_id so that
 * resume / multi-hook invocations all target the same OV session.
 * This replaces the old one-shot session model (create → add → extract → delete)
 * with a persistent session that lets OV's own commit/extract pipeline run.
 *
 * Format:
 *   parent:    cc-<ccSessionId>
 *   subagent:  cc-<ccSessionId>__subagent-<subagentId>
 *
 * The CC session_id is preserved verbatim so the OV id is human-readable and
 * the parent/subagent lineage is visible at a glance.
 *
 * Works with endpoints in openviking/server/routers/sessions.py:
 *   - POST   /api/v1/sessions/{id}/messages           (auto_create=true by default)
 *   - POST   /api/v1/sessions/{id}/commit
 *   - GET    /api/v1/sessions/{id}?auto_create=true
 *   - GET    /api/v1/sessions/{id}/context?token_budget=N
 */

import {
  deriveHarnessSessionId,
  isBypassed,
} from "../shared/session-model.mjs";

/**
 * Check whether a CC session_id or cwd matches any bypass pattern.
 * Also honours OPENVIKING_BYPASS_SESSION env var (via cfg.bypassSession).
 */
export { isBypassed };

/**
 * Derive a stable OV session ID from a CC session_id.
 *
 * Optionally append a suffix (e.g. subagent_id) for session isolation. The suffix
 * is normalized: `:` → `-` (so `subagent:abc123` → `subagent-abc123`) and any
 * characters outside [A-Za-z0-9._-] become `-`. Result: `cc-<uuid>__<suffix>`.
 */
export function deriveOvSessionId(ccSessionId, suffix = "") {
  return deriveHarnessSessionId("cc-", ccSessionId, suffix);
}

/**
 * Build a fetchJSON closure tied to a given config. Callers pass their own cfg
 * (from scripts/config.mjs loadConfig()) so the timeout can vary per hook.
 */
export function makeFetchJSON(cfg, timeoutKey = "timeoutMs") {
  const timeoutMs = Math.max(1000, cfg[timeoutKey] || cfg.timeoutMs || 10000);
  return async function fetchJSON(path, init = {}, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const headers = { "Content-Type": "application/json" };
      if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      if (cfg.accountId) headers["X-OpenViking-Account"] = cfg.accountId;
      if (cfg.userId) headers["X-OpenViking-User"] = cfg.userId;
      const actorPeerId = options.actorPeerId ?? "";
      if (actorPeerId) headers["X-OpenViking-Actor-Peer"] = actorPeerId;
      const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || body.status === "error") {
        return { ok: false, status: res.status, error: body.error || { message: `HTTP ${res.status}` } };
      }
      return { ok: true, result: body.result ?? body };
    } catch (err) {
      return { ok: false, error: { message: err?.message || String(err) } };
    } finally {
      clearTimeout(timer);
    }
  };
}

export function isRetryableFailure(res) {
  if (!res || res.ok) return false;
  const status = Number(res.status || 0);
  return !status || status >= 500 || status === 408 || status === 429;
}

function warnNonRetryable(operation, res) {
  const status = res?.status || "unknown";
  const msg = res?.error?.message || res?.error?.code || "";
  process.stderr.write(
    `[ov] ${operation} failed with non-retryable status ${status}; not enqueuing pending retry` +
      (msg ? ` (${msg})` : "") +
      "\n",
  );
}

export async function enqueuePendingDirectly(type, sessionId, payload = {}) {
  try {
    const { enqueue } = await import("./pending-queue.mjs");
    return await enqueue(type, sessionId, payload);
  } catch {
    return { ok: false };
  }
}

/**
 * Add a message to the persistent OV session. The server auto-creates the
 * session on first message via /sessions/{id}/messages (see add_message in
 * openviking/server/routers/sessions.py).
 *
 * `payload` accepts either { role, content } (simple string) or
 * { role, parts: [...] } (parts-mode, for tier-1 structured capture).
 */
export async function addMessage(fetchJSON, sessionId, payload) {
  const res = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    if (isRetryableFailure(res)) {
      const queued = await enqueuePendingDirectly("addMessage", sessionId, payload);
      if (queued.ok) res.pendingQueued = true;
      else res.pendingEnqueueFailed = true;
    } else {
      warnNonRetryable("addMessage", res);
    }
  }
  return res;
}

/**
 * Commit the persistent OV session (archive + background extract). Safe to
 * call repeatedly: if there are no pending messages the server is a no-op.
 */
export async function commitSession(fetchJSON, sessionId, payload = {}) {
  const res = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });
  if (!res.ok) {
    if (isRetryableFailure(res)) {
      const queued = await enqueuePendingDirectly("commitSession", sessionId, {});
      if (queued.ok) res.pendingQueued = true;
      else res.pendingEnqueueFailed = true;
    } else {
      warnNonRetryable("commitSession", res);
    }
  }
  return res;
}

/**
 * Get assembled session context (includes latest_archive_overview).
 * Returns null when the session does not exist or the request fails.
 */
export async function getSessionContext(fetchJSON, sessionId, tokenBudget = 128000) {
  const res = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
  );
  return res.ok ? res.result : null;
}

/**
 * Fetch session meta. Returns null if the session does not exist (unless
 * autoCreate=true).
 */
export async function getSession(fetchJSON, sessionId, { autoCreate = false } = {}) {
  const q = autoCreate ? "?auto_create=true" : "";
  const res = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}${q}`);
  return res.ok ? res.result : null;
}
