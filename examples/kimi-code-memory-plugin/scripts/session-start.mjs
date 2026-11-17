#!/usr/bin/env node

/**
 * SessionStart: bounded pending replay and the exit compensation sweep.
 *
 * The host fires this with `source` = `startup`, `resume`, `clear` or
 * `reclaimed`, and skips it entirely for `fork` and `reclaimed`. Its output is
 * discarded, so this entry prints nothing.
 *
 * What it does depends on the source:
 *
 *   startup, clear   replay this session's queue, then sweep states that have an
 *                    ended marker (bounded, oldest first, at most one commit)
 *   resume           record `resumedAt` and defer the session's own queued
 *                    commit until a new complete turn is delivered. A resume is
 *                    not an ending, so it must not run the sweep.
 *   anything else    nothing; a fork initialises lazily on its next event
 */

import { createAgentLogger, makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { captureSession } from "./capture-session.mjs";
import { emitNothing, runSafely } from "./envelope.mjs";
import { deadlineFor, payloadCwd, payloadSessionId, saveState, withSessionStage } from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";
import { runStartupSweep } from "./sweep.mjs";

const startedAt = Date.now();
const deadline = deadlineFor("session-start", startedAt);

async function main() {
    await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: emitNothing,
  }, async ({ cfg, input, cwd, sessionId }) => {
    const { log, logError } = createAgentLogger("kimicode", "session-start", cfg);
    const source = String(input.source || "unknown");

    await runSafely(logError, async () => {
      if (!sessionId) {
        log("skip", { reason: "no_session_id" });
        return;
      }
      if (source === "fork" || source === "reclaimed") {
        log("skip", { reason: source });
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
          const next = {
            ...state,
            startSource: source,
            lastStartAt: startedAt,
          };
          if (source === "resume") {
            // The marker and any queued commit belong to the previous generation.
            next.resumedAt = startedAt;
            for (const [agentId, agent] of Object.entries(next.agents || {})) {
              next.agents[agentId] = {
                ...agent,
                resumeBlockedCommit: true,
                // Where this generation starts. The hold may only be released by
                // a complete turn this generation itself produced, which is a
                // turn whose first record is at or after this line.
                resumeBoundaryLine: Number.isFinite(agent.scanLine) ? agent.scanLine + 1 : 1,
              };
            }
            log("resume", { resumedAt: startedAt });
          }

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
            state: next,
            mode: source === "resume" ? "resume" : "startup",
            now: Date.now(),
          });
          await saveState(sessionId, capture.state);
          log("capture", { status: capture.summary.status, agents: Object.keys(capture.summary.agents).length });
          return capture.state;
        },
      });

      if (result === null) {
        log("skip", { reason: "lock_busy" });
        return;
      }
      if (result.status === "state_unreadable") {
        log("skip", { reason: "state_unreadable" });
        return;
      }

      // A resume must not run the sweep: an old marker would seal the session the
      // user just reopened.
      if (source === "startup" || source === "clear") {
        const swept = await runStartupSweep({
          cfg,
          cwd,
          log,
          logError,
          deadline,
          fetchJSON,
          excludeSessionId: sessionId,
        });
        log("sweep", swept);
      }
    });
    });
}

// A hook must never hand the host an exception: the memory plugin failing is
// not a reason to block the user's turn, and every event has a legal empty
// answer.
main().catch((error) => {
  process.stderr.write(`[ov] hook failed: ${error?.message || error}\n`);
});
