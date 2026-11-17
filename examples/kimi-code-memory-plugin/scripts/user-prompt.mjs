#!/usr/bin/env node

/**
 * UserPromptSubmit: capture what is already stable, then inject once.
 *
 * The order matters and is the design's: the previous turn's closed records are
 * delivered *before* recall, so the query is matched against a server that
 * already holds what the user just did. Capture is best-effort here — a failure
 * must not cost the user their prompt — so it is bounded to the window before
 * recall, and a failure only leaves the cursor where it was.
 *
 * Exactly one thing is printed: the injection text, as plain stdout, once. The
 * host wraps it in its own `<hook_result>` part for the same user message.
 */

import { createAgentLogger, makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { captureSession, ovSessionIdFor } from "./capture-session.mjs";
import { emitNothing, emitText, runSafely } from "./envelope.mjs";
import {
  RECALL_MIN_RUN_MS,
  captureDeadlineFor,
  deadlineFor,
  payloadCwd,
  payloadSessionId,
  recallDeadlineFor,
  saveState,
  withSessionStage,
} from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";
import { extractPromptText, runPromptInjection } from "./recall.mjs";

const startedAt = Date.now();
const deadline = deadlineFor("user-prompt", startedAt);
// Capture may use everything before the recall window; the window itself is
// timed from when recall actually starts, not from process start.
const captureDeadline = captureDeadlineFor(deadline);

let injected = "";

async function main() {
    await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: () => {
      // Printed once, after the stage has decided. The host treats this as the
      // hook's message on the user's own turn.
      if (injected) emitText("UserPromptSubmit", injected);
      else emitNothing();
    },
  }, async ({ cfg, input, cwd, sessionId }) => {
    const { log, logError } = createAgentLogger("kimicode", "user-prompt", cfg);
    const promptText = extractPromptText(input.prompt);

    await runSafely(logError, async () => {
      if (!sessionId) {
        log("skip", { reason: "no_session_id" });
        return;
      }
      const { fetchJSON, effectivePeer } = makeAgentFetchJSON(cfg, cwd, {
        defaultTimeoutMs: Math.max(500, captureDeadline - Date.now()),
        getRemainingMs: () => captureDeadline - Date.now(),
      });
      // The peer is resolved from the session's own cwd by the shared loader. The
      // sender stamps `peer_id` from it; an explicit empty string would suppress
      // the stamp and file the memory under no peer at all.
      const peerId = effectivePeer?.peerId || "";

      // ---- capture what is already closed ----------------------------------
      // Wrapped on its own: a capture failure (a lock that cannot be created, a
      // state write that fails) must not cost the user their recall, which is
      // the only thing this event actually shows them.
      const captured = await runSafely(logError, async () => withSessionStage({
        sessionId,
        log,
        logError,
        deadline: captureDeadline,
        stage: async (state) => {
          const capture = await captureSession({
            cfg,
            input,
            cwd,
            log,
            logError,
            deadline: captureDeadline,
            fetchJSON,
            peerId,
            sessionId,
            state,
            mode: "normal",
            now: Date.now(),
          });
          await saveState(sessionId, capture.state);
          log("capture", { status: capture.summary.status });
          return capture.state;
        },
      }));
      if (captured === null) log("skip", { reason: "lock_busy_capture" });
      else if (captured === undefined) log("skip", { reason: "capture_failed" });
      else if (captured.status === "state_unreadable") log("skip", { reason: "state_unreadable" });

      // ---- inject ----------------------------------------------------------
      // The state capture just wrote is the one recall has to read
      // (profileInjected, cache, breaker). A capture that overran its own
      // deadline can still leave too little for recall: starting anyway would
      // only burn a breaker failure on a request that cannot finish.
      const recallRemaining = deadline - Date.now();
      if (recallRemaining < RECALL_MIN_RUN_MS) {
        log("skip", { reason: "recall_budget_spent", remainingMs: recallRemaining });
        return;
      }
      const recallDeadline = recallDeadlineFor(deadline);
      const { fetchJSON: recallFetch } = makeAgentFetchJSON(cfg, cwd, {
        defaultTimeoutMs: Math.max(500, recallDeadline - Date.now()),
        getRemainingMs: () => recallDeadline - Date.now(),
      });

      // A second lock acquisition: capture may have taken a while.
      const result = await withSessionStage({
        sessionId,
        log,
        logError,
        deadline: recallDeadline,
        stage: async (state) => {
          const injection = await runPromptInjection({
            cfg,
            state,
            cwd,
            promptText,
            ovSessionId: ovSessionIdFor(sessionId, "main"),
            fetchJSON: recallFetch,
            log,
            deadline: recallDeadline,
            now: Date.now(),
          });
          await saveState(sessionId, {
            ...injection.state,
            lastPromptAt: Date.now(),
            lastActivity: Date.now(),
          });
          return injection;
        },
      });

      if (result === null) {
        log("skip", { reason: "lock_busy_inject" });
        return;
      }
      if (result.status === "state_unreadable") {
        log("skip", { reason: "state_unreadable" });
        return;
      }
      if (result.status === "budget_exhausted") {
        log("skip", { reason: "recall_lock_budget_spent" });
        return;
      }
      injected = result.text || "";
      log("inject", {
        bytes: injected.length,
        profileInjected: result.profileInjected,
        recalled: result.recalled,
        reasons: result.reasons,
      });
    });
    });
}

// A hook must never hand the host an exception: the memory plugin failing is
// not a reason to block the user's turn, and every event has a legal empty
// answer.
main().catch((error) => {
  process.stderr.write(`[ov] hook failed: ${error?.message || error}\n`);
});
