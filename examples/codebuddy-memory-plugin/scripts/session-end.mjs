#!/usr/bin/env node

/**
 * SessionEnd Hook for CodeBuddy.
 *
 * Fires when the CodeBuddy session closes. We commit the persistent OV session
 * so the final turn's pending messages become an archive — without this hook,
 * the last window of messages would linger as pending until the next
 * Stop/PreCompact on a resumed session.
 *
 * ⚠️ `SessionEnd` only fires in the interactive TUI (verified in P0); headless
 * `-p` and `--input-format stream-json` runs never emit it. Capture therefore
 * cannot rely on this hook alone — `Stop` carries the same responsibility
 * mid-session, and session-start replays anything queued while the server was
 * down.
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const baseCfg = loadConfig();
const { log, logError } = createLogger("session-end");
const fetchJSON = makeFetchJSON(baseCfg);

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

async function main() {
  // Write-path hook: gated by autoCapture so that disabling capture also
  // disables the final-commit triggered here. This runs against the hook's own
  // directory, before the payload names the session's.
  if (!baseCfg.autoCapture) {
    log("skip", { reason: "disabled" });
    approve();
    return;
  }

  // The hook itself is bounded by the host timeout (2s observed killing the
  // hook process), but the commit below is a network call — detach so the
  // worker outlives the host's kill while the parent returns immediately.
  if (await maybeDetach(baseCfg, { approve })) return;

  await runHookStage({
    loadConfig,
    input: { read: readHookStdin, tolerant: true },
    gates: { enabled: (cfg) => cfg.autoCapture },
    envelope: approve,
    onSkip: (reason) => log("skip", { reason }),
  }, async ({ sessionId }) => {
    if (!sessionId) {
      log("skip", { reason: "no session_id" });
      return;
    }

    const ovSessionId = deriveOvSessionId(sessionId);
    const health = await fetchJSON("/health");
    if (!health.ok && isRetryableFailure(health)) {
      const queued = await enqueuePendingDirectly("commitSession", ovSessionId, {});
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
      enqueueFailed: Boolean(res.pendingEnqueueFailed),
      error: res.ok ? undefined : res.error?.message,
    });
  });
}

main().catch((err) => { logError("uncaught", err); approve(); });
