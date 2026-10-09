#!/usr/bin/env node

/**
 * SessionStart hook for the CodeBuddy OpenViking memory plugin.
 *
 * P1 scope (this revision): prove the host contract end to end — the plugin
 * loads, `${CODEBUDDY_PLUGIN_ROOT}` is substituted in hooks.json, and the shared
 * configuration resolves with harness=`codebuddy`. It performs no network I/O
 * and injects nothing, so it is safe to load while the rest is built.
 *
 * P3 adds, in this order:
 *   1. pending-write replay (independent of injection — a user may disable
 *      injection and still expect earlier failed writes to be recovered), then
 *   2. profile/catalog injection and, for `resume`/`compact`, the archive block,
 *      composed into one `<openviking-context source="...">` payload returned
 *      through `hookSpecificOutput.additionalContext`.
 *
 * Host notes that shape this hook (docs/HOST-CONTRACT.md):
 *   §3  `SessionStart` may fire more than once per session (observed twice under
 *       `--input-format stream-json`), so every step must be idempotent.
 *   §4  the payload has no `cwd`; `process.cwd()` matched the session cwd in the
 *       probe, so the cwd-based config reload keeps its default.
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("session-start");

function approve(additionalContext) {
  const out = { decision: "approve" };
  if (additionalContext) {
    out.hookSpecificOutput = {
      hookEventName: "SessionStart",
      additionalContext,
    };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
}

runHookStage({
  loadConfig,
  input: { tolerant: true },
  envelope: approve,
  onSkip: (reason) => log("skip", { reason }),
}, async ({ cfg, input, cwd, sessionId }) => {
  const source = (input && input.source) || "startup";

  // P1: configuration resolution is the observable. Everything below is
  // deliberately side-effect free — no fetch, no injection, no state writes.
  log("config", {
    source,
    sessionId,
    cwd,
    baseUrl: cfg.baseUrl,
    account: cfg.account,
    user: cfg.user,
    configPath: cfg.configPath,
    credentialPath: cfg.credentialPath,
    autoRecall: cfg.autoRecall,
    autoCapture: cfg.autoCapture,
    noAutoInject: cfg.noAutoInject,
    debug: cfg.debug,
  });
}).catch((err) => { logError("uncaught", err); approve(); });
