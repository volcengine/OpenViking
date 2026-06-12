#!/usr/bin/env node

/**
 * SessionEnd Hook for Claude Code.
 *
 * Fires when the CC session closes. We commit the persistent OV session so
 * the final turn's pending messages become an archive — without this hook,
 * the last window of messages would linger as pending until the next
 * Stop/PreCompact on a resumed session.
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  isBypassed,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("session-end");
const fetchJSON = makeFetchJSON(cfg);

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

async function main() {
  // Write-path hook: gated by autoCapture so that disabling capture also
  // disables the final-commit triggered here.
  if (!cfg.autoCapture) {
    log("skip", { reason: "autoCapture disabled" });
    approve();
    return;
  }

  if (await maybeDetach(cfg, { approve })) return;

  let input = {};
  try {
    input = JSON.parse((await readHookStdin()) || "{}");
  } catch { /* best effort */ }

  const sessionId = input.session_id;
  const cwd = input.cwd;
  if (!sessionId) {
    log("skip", { reason: "no session_id" });
    approve();
    return;
  }

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  const ovSessionId = deriveOvSessionId(sessionId);
  const health = await fetchJSON("/health");
  if (!health.ok && isRetryableFailure(health)) {
    const queued = await enqueuePendingDirectly("commitSession", ovSessionId, {});
    log("commit", { ovSessionId, ok: false, queued: queued.ok, reason: "health_retryable" });
    approve();
    return;
  }
  if (!health.ok) {
    logError("health_check", `non-retryable status ${health.status || "unknown"}`);
    approve();
    return;
  }

  const res = await commitSession(fetchJSON, ovSessionId);
  log("commit", {
    ovSessionId,
    ok: res.ok,
    queued: Boolean(res.pendingQueued),
    enqueueFailed: Boolean(res.pendingEnqueueFailed),
    error: res.ok ? undefined : res.error?.message,
  });
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
