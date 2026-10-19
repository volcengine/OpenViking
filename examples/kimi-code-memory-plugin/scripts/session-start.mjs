#!/usr/bin/env node

/**
 * SessionStart hook for Kimi Code (startup / resume / clear).
 *
 * One job (DESIGN.md §3.3 / §7): replay a bounded batch of the pending queue —
 * messages that failed to send during earlier hooks (server down, budget
 * exhausted). The bound defaults to 2 here (OPENVIKING_PENDING_REPLAY_LIMIT
 * overrides) so a large backlog cannot stall session startup. The default
 * cannot live in the manifest: kimi-code's hook schema accepts only
 * event/matcher/command/timeout, and an unsupported field drops the whole
 * hook.
 *
 * SessionStart's stdout is DISCARDED by Kimi (issue #2873, open) — this hook
 * never injects anything; profile injection rides the first UserPromptSubmit
 * instead (auto-recall.mjs). It also never commits: committing is
 * SessionEnd's job, with the threshold path in auto-capture.
 *
 * Everything runs synchronously — there is no detached worker whose death
 * would need an end-marker sweep — so resume needs no state cleanup here.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { boundFetch, createRemainingMs } from "./hook-budget.mjs";
import { deriveOvSessionId } from "./session-state.mjs";
import { replayAgentPending, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";

const { log, logError } = createLogger("session-start");

// Bound the pending replay for this entry point. Kimi-code's hook manifest
// schema is strict (event/matcher/command/timeout only), so a per-hook env
// field would get the whole hook dropped — the default lives here instead;
// OPENVIKING_PENDING_REPLAY_LIMIT still overrides it.
process.env.OPENVIKING_PENDING_REPLAY_LIMIT ||= "2";

// Health plus a bounded replay must finish under the manifest's 30s; without
// one shared clock each request would get the full 15s again (15 + 2×15 = 45s)
// and a host kill would strand a claimed queue entry for ten minutes.
const HOOK_BUDGET_MS = 20_000;
const HEALTH_TIMEOUT_MS = 3_000;
const remainingMs = createRemainingMs(HOOK_BUDGET_MS);

let cfg = loadConfig();
let fetchJSON = boundFetch(
  createOvHttp(cfg, { defaultTimeoutMs: cfg.timeoutMs }),
  remainingMs,
  cfg.timeoutMs,
);

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
    fetchJSON = boundFetch(createOvHttp(cfg, {
      defaultTimeoutMs: cfg.timeoutMs,
      requireJsonBody: true,
    }), remainingMs, cfg.timeoutMs);
    log("start", { source, sessionId: stage.sessionId, ovSessionId: stage.sessionId ? deriveOvSessionId(stage.sessionId) : "" });

    const health = await fetchJSON("/health", {}, { timeoutMs: HEALTH_TIMEOUT_MS });
    if (!health.ok) {
      logError("health_check", "server unreachable; skipping pending replay");
      return;
    }

    await replayPendingWrites();
  },
).catch((error) => {
  logError("session-start", error?.message || String(error));
  process.exit(0);
});
