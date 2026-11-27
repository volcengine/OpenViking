#!/usr/bin/env node

/**
 * PreCompact Hook for Claude Code.
 *
 * Fires before CC rewrites the transcript via /compact (manual or auto).
 *
 * Catch-up first: the turn in progress has had no Stop yet, so without it
 * nothing of that turn is on the server when the model, right after an
 * auto-compaction in the middle of a long turn, looks it up in its session
 * history. The Stop capture runs over the same transcript, in its own process
 * as the Stop hook's detached worker does. The hook waits for it up to
 * CATCH_UP_WAIT_MS; a push still running then lands in the live tail after
 * the commit.
 *
 * Then, unless autoCommitOnCompact is off, the persistent OV session is
 * committed so pending messages become an archive before the transcript is
 * mutated. PreCompact has no
 * additionalContext output (platform-verified), so injection happens later in
 * session-start.mjs when source="compact".
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  commitSession,
  deriveOvSessionId,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { catchUpTranscript } from "./lib/catch-up.mjs";
import { enqueueCommitAfterWrites } from "./lib/pending-queue.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { commitsOnCompaction } from "./shared/session-model.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("pre-compact");
// After the catch-up wait, health + commit share what is left of the hook's
// 30s; a commit that does not make it is queued and replayed later.
const AFTER_CATCH_UP_TIMEOUT_MS = 4000;
const fetchJSON = makeFetchJSON({ ...loadConfig(), timeoutMs: AFTER_CATCH_UP_TIMEOUT_MS });

const CATCH_UP_WAIT_MS = (() => {
  const v = Number(process.env.OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS);
  return Number.isFinite(v) && v >= 0 ? Math.floor(v) : 20000;
})();

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

runHookStage({
  loadConfig,
  input: { tolerant: true },
  // Write-path hook: gated by autoCapture so that disabling capture also
  // disables the pending-message commits triggered here.
  gates: { enabled: (cfg) => cfg.autoCapture },
  envelope: approve,
  onSkip: (reason) => log("skip", { reason }),
}, async ({ cfg, sessionId, raw }) => {
  if (!sessionId) {
    log("skip", { reason: "no session_id" });
    return;
  }

  log("catch_up", await catchUpTranscript(raw, CATCH_UP_WAIT_MS));

  const ovSessionId = deriveOvSessionId(sessionId);
  if (!commitsOnCompaction(cfg)) {
    log("skip", { stage: "commit", reason: "autoCommitOnCompact off", ovSessionId });
    return;
  }
  const health = await fetchJSON("/health");
  if (!health.ok && isRetryableFailure(health)) {
    const queued = await enqueueCommitAfterWrites(ovSessionId, {}, { origin: fetchJSON.origin });
    log("commit", { ovSessionId, ok: false, queued: queued.ok, reason: "health_retryable" });
    return;
  }
  if (!health.ok) {
    logError("health_check", `non-retryable status ${health.status || "unknown"}`);
    return;
  }

  const res = await commitSession(fetchJSON, ovSessionId);
  log("commit", {
    ovSessionId,
    ok: res.ok,
    trace_id: res.traceId || res.result?.trace_id,
    queued: Boolean(res.pendingQueued),
    error: res.ok ? undefined : res.error?.message,
  });
}).catch((err) => { logError("uncaught", err); approve(); });
