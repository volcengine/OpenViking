/**
 * When a session is committed. The plugin commits at a compaction and at a
 * session end. In between OpenViking commits by itself: the session carries
 * this config's auto-commit policy, and the server checks the token threshold
 * on every message write and the idle time where its idle commit is on.
 * Needs a server with session auto-commit policies (0.4.13 or later).
 */

import { commitAgentSession } from "../agent-hook-runtime.mjs";

// The shortest idle timeout the server accepts; 0 turns the idle commit off.
const MIN_IDLE_COMMIT_SECONDS = 300;

export const COMMIT_BODY = { keep_recent_count: 0 };

/**
 * The auto-commit policy this config asks the server to hold. Every field is
 * named: the server fills a missing one from its own defaults. The idle commit
 * only runs where the server has `memory.session_auto_commit.enabled`.
 */
export function sessionCommitPolicy(cfg) {
  const idle = Number(cfg.commitIdleSeconds) || 0;
  return {
    pending_token_threshold: cfg.commitTokenThreshold,
    message_count_threshold: 0,
    idle_timeout_seconds: idle > 0 ? Math.max(MIN_IDLE_COMMIT_SECONDS, idle) : 0,
    keep_recent_count: COMMIT_BODY.keep_recent_count,
    min_commit_interval_seconds: 0,
  };
}

/**
 * After a capture that sent `sent` messages and settled `captured` of them:
 * commit at a boundary ("compact" or "end"), otherwise make sure the session
 * holds the policy. Returns the state fields that changed.
 */
export async function commitWhenDue(ctx, state, { boundary, sent, captured }) {
  const { cfg, fetchJSON, sessionId, log } = ctx;
  const next = { capturedSinceCommit: Number(state.capturedSinceCommit || 0) + captured };
  if (boundary) {
    if (next.capturedSinceCommit > 0 && (boundary !== "compact" || cfg.autoCommitOnCompact !== false)) {
      const committed = await commitAgentSession(fetchJSON, sessionId, log, COMMIT_BODY);
      if (committed.ok || committed.pendingQueued) next.capturedSinceCommit = 0;
      if (committed.ok) ctx.outcome.committed = true;
    }
    return next;
  }
  // The session exists once a message reached the server; a changed config
  // sends its policy again.
  const policy = sessionCommitPolicy(cfg);
  const key = JSON.stringify(policy);
  if (sent > 0 && state.commitPolicy !== key) {
    const result = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/config`, {
      method: "PATCH",
      body: JSON.stringify({ auto_commit_policy: policy }),
    });
    log("commit_policy", { ok: result.ok, status: result.status });
    if (result.ok) next.commitPolicy = key;
  }
  return next;
}
