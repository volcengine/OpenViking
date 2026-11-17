/**
 * Shared plumbing for the six hook entries.
 *
 * Each entry is short on purpose: read the payload through `runHookStage`, take
 * the session lock, load state, run its stage, write state. What lives here is
 * the part every entry must get identical — the lock, the state read that
 * distinguishes a missing file from a corrupt one, quarantine of a corrupt one,
 * and a bounded deadline that is always smaller than the manifest's timeout.
 */

import {
  quarantineHookState,
  readHookStateDetailed,
  withAgentHookLock,
  writeHookState,
} from "./shared/agent-hook-runtime.mjs";
import { HARNESS } from "./config.mjs";
import { emptySessionState } from "./session-state.mjs";

/**
 * Internal budgets, in milliseconds. Each is smaller than the manifest timeout
 * for that event so the hook always answers before the host kills it; a hook
 * killed at the manifest timeout loses whatever it had not written.
 *
 * The gap is not padding: the host's own process teardown, the state write and
 * the final stdout write all happen after the stage returns.
 */
export const BUDGETS = {
  "session-start": 3000,
  // Capture and recall share one entry. Capture runs first, so it may only use
  // the part before the reserved recall window; recall then gets its own budget
  // from the moment it actually starts, not from process start.
  "user-prompt": 15000,
  recall: 8000,
  stop: 6000,
  "pre-compact": 10000,
  "session-end": 5000,
  "uri-guard": 2000,
};

/**
 * A recall shorter than this is not worth starting.
 *
 * The reserved window is normally the full `BUDGETS.recall`, but a capture that
 * overran its own deadline can still leave less. A sub-second request would
 * fail and count against the breaker, so the entry skips instead.
 */
export const RECALL_MIN_RUN_MS = 1500;

/** The deadline for one entry, as an absolute timestamp. */
export function deadlineFor(name, startedAt = Date.now()) {
  return startedAt + (BUDGETS[name] || 5000);
}

/** Where capture must stop so the recall window starts on time. */
export function captureDeadlineFor(entryDeadline) {
  return entryDeadline - BUDGETS.recall;
}

/**
 * Recall's own deadline: its full budget from now, never past the entry's.
 *
 * Computing it from process start was the bug this replaces: capture could
 * spend the whole entry, leaving recall with only the 500ms fetch floor and a
 * guaranteed failure that the breaker then counted.
 */
export function recallDeadlineFor(entryDeadline, now = Date.now()) {
  return Math.min(entryDeadline, now + BUDGETS.recall);
}

/**
 * Run `stage` under the session lock with a loaded state.
 *
 * Returns `null` when the lock could not be taken. That is not an error: the
 * shared lock waits up to five seconds and then gives up, and the correct
 * response to "another hook owns this session" is to do nothing and let the
 * next boundary do the work.
 *
 * `readState` distinguishes the four ways a state read can end, because the
 * response differs: `missing` starts fresh, `unreadable` keeps every cursor and
 * does nothing, and `corrupt` is quarantined before a fresh state is written.
 */
export async function withSessionStage({
  sessionId,
  log,
  logError,
  readState = true,
  // The entry's own budget. The lock wait is part of that budget, not an extra
  // five seconds on top of it: a SessionStart with three seconds in total must
  // not spend five waiting and then be killed with nothing written.
  deadline,
  stage,
}) {
  const waitMs = Number.isFinite(deadline) ? Math.max(0, deadline - Date.now()) : 5000;
  if (waitMs <= 0) {
    log("skip", { reason: "lock_budget_spent" });
    return { status: "budget_exhausted" };
  }
  return withAgentHookLock(HARNESS, sessionId, async () => {
    let state = emptySessionState();
    if (readState) {
      const read = await readHookStateDetailed(HARNESS, sessionId);
      if (read.status === "unreadable") {
        logError("state_unreadable", read.error);
        return { status: "state_unreadable", error: read.error };
      }
      if (read.status === "corrupt") {
        // A corrupt state cannot be resumed: its cursors are gone. Quarantine
        // keeps the bytes for a maintainer, and the session starts a new
        // generation from the file end rather than replaying its whole wire.
        const moved = await quarantineHookState(HARNESS, sessionId);
        if (!moved.ok) {
          // The original could not be moved aside. Starting fresh anyway would
          // overwrite it on the next write and destroy the only local record of
          // what was already delivered, so this stage stops instead.
          logError("state_quarantine_failed", moved.error);
          return { status: "state_quarantine_failed", error: moved.error };
        }
        log("state_quarantined", { backup: moved.backup, error: read.error });
        state = emptySessionState();
        state.corruptRecovered = true;
      } else {
        state = { ...emptySessionState(), ...read.state };
      }
    }
    return stage(state);
  }, { waitMs });
}

/** Persist one session's state. */
export async function saveState(sessionId, state) {
  await writeHookState(HARNESS, sessionId, state);
}

/** The payload's session id, which the host always sends as `session_id`. */
export function payloadSessionId(input = {}) {
  return String(input.session_id ?? input.sessionId ?? "");
}

/** The payload's working directory, which selects the workspace config layer. */
export function payloadCwd(input = {}) {
  return String(input.cwd || input.workspace_roots?.[0] || process.cwd());
}

/** The event name the host puts in the payload, for logging. */
export function payloadEvent(input = {}) {
  return String(input.hook_event_name || input.hookEventName || "");
}
