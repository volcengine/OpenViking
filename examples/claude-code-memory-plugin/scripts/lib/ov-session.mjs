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
 * Everything past that derivation is the shared hook runtime under the names
 * these scripts already call it by.
 */

import {
  addAgentMessage,
  commitAgentSession,
  enqueueAgentPending,
  getAgentSession,
  getAgentSessionContext,
  makeAgentFetchJSON,
} from "../shared/agent-hook-runtime.mjs";
import { isRetryableFailure } from "../shared/retryable.mjs";
import {
  deriveHarnessSessionId,
  isBypassed,
} from "../shared/session-model.mjs";

/**
 * Check whether a CC session_id or cwd matches any bypass pattern.
 * Also honours OPENVIKING_BYPASS_SESSION env var (via cfg.bypassSession).
 */
export { isBypassed, isRetryableFailure };

export {
  addAgentMessage as addMessage,
  enqueueAgentPending as enqueuePendingDirectly,
  getAgentSession as getSession,
  getAgentSessionContext as getSessionContext,
};

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
  return makeAgentFetchJSON(cfg, process.cwd(), {
    defaultTimeoutMs: cfg[timeoutKey] || cfg.timeoutMs || 10000,
    // Every call on this stack names the peer it wants, so nothing here may put
    // one on a request that asked for none.
    getActorPeerId: () => "",
  }).fetchJSON;
}

/**
 * Commit the persistent OV session (archive + background extract). Safe to
 * call repeatedly: if there are no pending messages the server is a no-op.
 */
export function commitSession(fetchJSON, sessionId, payload = {}) {
  return commitAgentSession(fetchJSON, sessionId, undefined, payload);
}
