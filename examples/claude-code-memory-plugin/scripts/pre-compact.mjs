#!/usr/bin/env node

/**
 * PreCompact Hook for Claude Code.
 *
 * Fires before CC rewrites the transcript via /compact (manual or auto).
 * We only commit the persistent OV session so pending messages become an
 * archive before the transcript is mutated. PreCompact has no
 * additionalContext output (platform-verified), so injection happens later
 * in session-start.mjs when source="compact".
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { commitSession, deriveOvSessionId, makeFetchJSON } from "./lib/ov-session.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("pre-compact");
const fetchJSON = makeFetchJSON(loadConfig());

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
}, async ({ sessionId }) => {
  if (!sessionId) {
    log("skip", { reason: "no session_id" });
    return;
  }

  const ovSessionId = deriveOvSessionId(sessionId);
  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    return;
  }

  const res = await commitSession(fetchJSON, ovSessionId);
  log("commit", {
    ovSessionId,
    ok: res.ok,
    trace_id: res.traceId || res.result?.trace_id,
    error: res.ok ? undefined : res.error?.message,
  });
}).catch((err) => { logError("uncaught", err); approve(); });
