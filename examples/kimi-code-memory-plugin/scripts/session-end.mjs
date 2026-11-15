#!/usr/bin/env node

/**
 * SessionEnd: write the marker first, then do as much as the budget allows.
 *
 * The host waits for this hook, but it also cannot guarantee it runs: a crash
 * or a kill loses it. So the marker is the durable part and the work after it
 * is best-effort. That ordering is deliberate — the sweep needs the marker to
 * know a session ended, and a marker written after a failed commit would leave
 * nothing behind at all.
 *
 * `reason === "reclaim"` is skipped by the host, so it is not handled here.
 * A session that never had state does not get one invented: this entry does not
 * create a state file just to have a marker in it.
 */

import { createAgentLogger, makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { captureSession } from "./capture-session.mjs";
import { emitNothing, runSafely } from "./envelope.mjs";
import { deadlineFor, payloadCwd, payloadSessionId, saveState, withSessionStage } from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";
import { agentSettled, settleEndedMarker } from "./session-state.mjs";

const deadline = deadlineFor("session-end");

async function main() {
    await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: emitNothing,
  }, async ({ cfg, input, cwd, sessionId }) => {
    const { log, logError } = createAgentLogger("kimicode", "session-end", cfg);

    await runSafely(logError, async () => {
      if (!sessionId) {
        log("skip", { reason: "no_session_id" });
        return;
      }
      const { fetchJSON, effectivePeer } = makeAgentFetchJSON(cfg, cwd, {
        defaultTimeoutMs: Math.max(500, deadline - Date.now()),
        getRemainingMs: () => deadline - Date.now(),
      });
      // The peer comes from the session's own cwd through the shared loader; an
      // explicit empty string would stop the sender stamping `peer_id`.
      const peerId = effectivePeer?.peerId || "";

      const result = await withSessionStage({
        sessionId,
        log,
        logError,
        deadline,
        stage: async (state) => {
          const hadState = Object.keys(state.agents || {}).length > 0;
          if (!hadState && !state.corruptRecovered) {
            // Never seen by this plugin: do not create a sweep target for a
            // session whose history was never captured.
            log("skip", { reason: "no_prior_state" });
            return { skipped: true };
          }

          const marker = {
            at: Date.now(),
            reason: typeof input.reason === "string" ? input.reason : "unknown",
          };
          // Persist the marker before anything that can fail. The whole point of
          // the marker is that the process may die during the capture or the
          // commit that follows; a marker kept only in memory until the end would
          // vanish exactly when it is needed.
          const marked = { ...state, endedMarker: marker, endedMarkerSeen: true };
          await saveState(sessionId, marked);
          log("marker", { reason: marker.reason, at: marker.at });

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
            state: marked,
            mode: "session-end",
            now: Date.now(),
          });

          const agents = Object.values(capture.state.agents || {});
          const allSettled = agents.length > 0 && agents.every(agentSettled);
          const settled = settleEndedMarker(capture.state, {
            allAgentsSettled: allSettled,
            at: Date.now(),
          });
          await saveState(sessionId, settled);
          log("end", {
            reason: marker.reason,
            allSettled,
            committed: capture.summary.committed,
            blocked: capture.summary.blocked,
          });
          return settled;
        },
      });

      if (result === null) log("skip", { reason: "lock_busy" });
      else if (result?.status === "state_unreadable") log("skip", { reason: "state_unreadable" });
    });
    });
}

// A hook must never hand the host an exception: the memory plugin failing is
// not a reason to block the user's turn, and every event has a legal empty
// answer.
main().catch((error) => {
  process.stderr.write(`[ov] hook failed: ${error?.message || error}\n`);
});
