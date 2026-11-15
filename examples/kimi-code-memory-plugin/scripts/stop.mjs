#!/usr/bin/env node

/**
 * Stop: the turn boundary.
 *
 * The host fires `Stop` after a step completes and does *not* fire it while
 * tool calls are pending, so this is where a closed turn is delivered. Its
 * stdout is consumed — a refusal reason would make the agent keep going — so
 * this entry never prints anything and never exits non-zero.
 *
 * A turn that is still open at this point is not sent: the host may fire Stop
 * between steps of a turn that is not finished. It is re-read from its own
 * start line at the next boundary.
 */

import { createAgentLogger, makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { captureSession } from "./capture-session.mjs";
import { emitNothing, runSafely } from "./envelope.mjs";
import { deadlineFor, payloadSessionId, saveState, withSessionStage } from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";

const deadline = deadlineFor("stop");

async function main() {
    await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: emitNothing,
  }, async ({ cfg, input, cwd, sessionId }) => {
    const { log, logError } = createAgentLogger("kimicode", "stop", cfg);

    await runSafely(logError, async () => {
      if (!sessionId) {
        log("skip", { reason: "no_session_id" });
        return;
      }
      const { fetchJSON, effectivePeer } = makeAgentFetchJSON(cfg, cwd, {
        defaultTimeoutMs: Math.max(500, deadline - Date.now()),
        getRemainingMs: () => deadline - Date.now(),
      });
      // The peer is resolved from the session's own cwd by the shared loader. The
      // sender stamps `peer_id` from it; an explicit empty string would suppress
      // the stamp and file the memory under no peer at all.
      const peerId = effectivePeer?.peerId || "";

      const result = await withSessionStage({
        sessionId,
        log,
        logError,
        deadline,
        stage: async (state) => {
          const capture = await captureSession({
            cfg,
            input,
            cwd,
            log,
            logError,
            deadline,
            fetchJSON,
            peerId,
            sessionId,
            state,
            mode: "normal",
            now: Date.now(),
          });
          await saveState(sessionId, capture.state);
          log("capture", {
            status: capture.summary.status,
            delivered: capture.summary.delivered,
            queued: capture.summary.queued,
            committed: capture.summary.committed,
            blocked: capture.summary.blocked,
          });
          return capture.state;
        },
      });

      if (result === null) log("skip", { reason: "lock_busy" });
      else if (result.status === "state_unreadable") log("skip", { reason: "state_unreadable" });
    });
    });
}

// A hook must never hand the host an exception: the memory plugin failing is
// not a reason to block the user's turn, and every event has a legal empty
// answer.
main().catch((error) => {
  process.stderr.write(`[ov] hook failed: ${error?.message || error}\n`);
});
