/**
 * Persistent OpenViking session helpers for CodeBuddy hooks.
 *
 * ovSessionId is deterministically derived from the CodeBuddy session_id so
 * that resume / multi-hook invocations all target the same OV session, and so
 * that auto-recall and auto-capture agree on which session they are reading
 * and writing.
 *
 * Format:
 *   parent:    cb-<sessionId>
 *   subagent:  cb-<sessionId>__subagent-<agentId>
 *
 * The CodeBuddy session_id is preserved verbatim so the OV id is readable and
 * the parent/subagent lineage is visible at a glance. Subagent lineage matters
 * here: `SubagentStart`/`SubagentStop` hand the subagent its own transcript
 * (docs/HOST-CONTRACT.md §4), so a subagent can be captured as its own OV
 * session under this derived id.
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
 * Check whether a CodeBuddy session_id or cwd matches any bypass pattern.
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
 * Derive a stable OV session ID from a CodeBuddy session_id.
 *
 * Optionally append a suffix (e.g. an agent_id) for session isolation. The
 * suffix is normalized: `:` → `-` and any characters outside [A-Za-z0-9._-]
 * become `-`. Result: `cb-<uuid>__<suffix>`.
 */
export function deriveOvSessionId(cbSessionId, suffix = "") {
  return deriveHarnessSessionId("cb-", cbSessionId, suffix);
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
