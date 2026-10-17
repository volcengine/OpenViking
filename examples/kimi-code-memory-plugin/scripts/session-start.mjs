#!/usr/bin/env node

/**
 * SessionStart hook for Kimi Code (startup / resume / clear).
 *
 * Two jobs (DESIGN.md §3.3 / §7):
 *   1. Replay a bounded batch of the pending queue — messages that failed to
 *      send during earlier hooks (server down, budget exhausted). The bound
 *      is OPENVIKING_PENDING_REPLAY_LIMIT (this plugin's manifest sets 2) so
 *      a large backlog cannot stall session startup.
 *   2. On resume, clear stale `.ended` markers for THIS session so a marker
 *      left by a previous exit cannot make the sweep commit a live session.
 *
 * SessionStart's stdout is DISCARDED by Kimi (issue #2873, open) — this hook
 * never injects anything; profile injection rides the first UserPromptSubmit
 * instead (auto-recall.mjs). It also never commits: committing is
 * SessionEnd's job, with the threshold path in auto-capture.
 *
 * Structure mirrors examples/codex-memory-plugin/scripts/session-start-commit.mjs,
 * minus the injection and sweep this host does not need.
 */

import { readdir, rm } from "node:fs/promises";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { deriveOvSessionId, getStateDir, safeId } from "./session-state.mjs";
import { replayAgentPending, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";

const { log, logError } = createLogger("session-start");

let cfg = loadConfig();
let fetchJSON = createOvHttp(cfg, { defaultTimeoutMs: cfg.timeoutMs });

/**
 * Drain writes an earlier hook queued while the server was unreachable.
 * SessionStart runs after a known-healthy check, so it is where the queue
 * gets its chance. A bypassed directory still replays: the entries were
 * recorded by sessions that were not bypassed.
 */
async function replayPendingWrites() {
  try {
    const result = await replayAgentPending(fetchJSON, log);
    if (result.replayed > 0 || result.failed > 0 || result.deferred > 0) {
      log("pending-replay", result);
    }
  } catch (error) {
    logError("pending-replay", error?.message || String(error));
  }
}

/** End markers whose session is live again must not trigger a commit sweep. */
async function clearEndedMarkers(sessionId) {
  const prefix = `${safeId(sessionId)}.ended.`;
  try {
    const dir = getStateDir();
    for (const name of await readdir(dir)) {
      if (name.startsWith(prefix)) await rm(`${dir}/${name}`, { force: true });
    }
    log("ended_markers_cleared", { sessionId });
  } catch {
    // No state dir yet: nothing to clear.
  }
}

runHookStage(
  {
    clientId: "kimicode",
    loadConfig: (cwd) => {
      cfg = loadConfig(cwd);
      return cfg;
    },
    envelope: () => {},
    onSkip: (reason) => log("skip", { stage: "init", reason }),
  },
  async (stage) => {
    const source = stage.input?.source || "startup";
    cfg = stage.cfg;
    fetchJSON = createOvHttp(cfg, {
      defaultTimeoutMs: cfg.timeoutMs,
      requireJsonBody: true,
    });
    log("start", { source, sessionId: stage.sessionId, ovSessionId: deriveOvSessionId(stage.sessionId) });

    const health = await fetchJSON("/health");
    if (!health.ok) {
      logError("health_check", "server unreachable; skipping pending replay");
      return;
    }

    if (source === "resume") {
      // Earliest liveness signal after a resume: this session is running
      // again, so markers from its previous exit must not trigger a commit.
      await clearEndedMarkers(stage.sessionId);
    }

    await replayPendingWrites();
  },
).catch((error) => {
  logError("session-start", error?.message || String(error));
  process.exit(0);
});
