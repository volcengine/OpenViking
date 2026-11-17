#!/usr/bin/env node

/**
 * PreToolUse: refuse a `viking://` URI handed to a local file tool.
 *
 * A virtual URI is not a path, so `Read`, `Glob`, `Grep`, `Edit`, `Write` and
 * `ReadMediaFile` cannot succeed with one; the call is denied with the MCP tool
 * that can answer it instead. Nothing else is touched: a local path runs as
 * before, and a shell command is never blocked, because a `viking://` string in
 * a command line is usually data (`ov read …`, an HTTP body, a search pattern)
 * rather than a file.
 *
 * The host does not consume `additionalContext` on this event, so a shell notice
 * would be invisible; the Skills explain the MCP route instead. The deny is a
 * normal answer: exit code 0, one JSON object on stdout.
 */

import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { evaluateUriGuard, readToolEvent } from "./shared/uri-guard.mjs";
import { emitDeny, emitNothing, runSafely } from "./envelope.mjs";
import { payloadSessionId } from "./hook-stage.mjs";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./shared/debug-log.mjs";

let decision = null;

async function main() {
  await runHookStage({
    clientId: "kimicode",
    loadConfig,
    sessionId: payloadSessionId,
    envelope: () => {
      if (decision) emitDeny(decision.reason);
      else emitNothing();
    },
  }, async ({ cfg, input }) => {
    const { log, logError } = createLogger("kimicode:uri-guard", cfg);
    await runSafely(logError, async () => {
      // `evaluateUriGuard` already returns null for a tool it has no hint for
      // and for a shell command, so a second hand-kept list here would only be
      // one more place to forget to update.
      const { toolName, toolInput } = readToolEvent(input);
      const verdict = evaluateUriGuard(toolName, toolInput);
      if (!verdict) return;
      decision = verdict;
      log("deny", { tool: String(toolName || "").toLowerCase(), uri: verdict.uri });
    });
  });
}

main().catch((error) => {
  process.stderr.write(`[ov] hook failed: ${error?.message || error}\n`);
});
