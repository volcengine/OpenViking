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
import { commitSession, deriveOvSessionId, isBypassed, makeFetchJSON } from "./lib/ov-session.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("pre-compact");
const fetchJSON = makeFetchJSON(cfg);

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

async function main() {
  // Write-path hook: gated by autoCapture so that disabling capture also
  // disables the pending-message commits triggered here.
  if (!cfg.autoCapture) {
    log("skip", { reason: "autoCapture disabled" });
    approve();
    return;
  }

  let input = {};
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString() || "{}");
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
  if (!health.ok) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  const res = await commitSession(fetchJSON, ovSessionId);
  log("commit", { ovSessionId, ok: res.ok, error: res.ok ? undefined : res.error?.message });
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
