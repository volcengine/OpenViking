#!/usr/bin/env node

/**
 * PreCompact: the last chance before the transcript is condensed.
 *
 * The host waits for this hook, so it is a real boundary rather than a
 * best-effort one: whatever is already closed is delivered, and if anything was
 * delivered and is not committed, it is committed regardless of the token and
 * turn thresholds. A short session that is being compacted is exactly the case
 * the thresholds would otherwise never reach.
 *
 * An open turn is *not* forced closed. If compaction happens mid-turn, the last
 * terminal boundary before it is committed and the open tail stays for a later
 * event — the alternative would archive a half-written step.
 *
 * Output is discarded by the host; nothing is printed.
 */

import { createAgentLogger, makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { captureSession } from "./capture-session.mjs";
import { emitNothing, runSafely } from "./envelope.mjs";
import { deadlineFor, payloadSessionId, saveState, withSessionStage } from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";

const deadline = deadlineFor("pre-compact");

async function main() {
    await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: emitNothing,
  }, async ({ cfg, input, cwd, sessionId }) => {
    const { log, logError } = createAgentLogger("kimicode", "pre-compact", cfg);

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
            mode: "compact",
            now: Date.now(),
          });
          await saveState(sessionId, {
            ...capture.state,
            lastCompactionAt: Date.now(),
            lastCompactionTrigger: typeof input.trigger === "string" ? input.trigger : capture.state.lastCompactionTrigger,
          });
          log("capture", {
            status: capture.summary.status,
            delivered: capture.summary.delivered,
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
