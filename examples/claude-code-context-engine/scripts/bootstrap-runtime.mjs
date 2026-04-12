#!/usr/bin/env node

/**
 * SessionStart hook: install MCP runtime dependencies + create OV session.
 */

import {
  computeSourceState,
  ensureRuntimeInstalled,
  getRuntimePaths,
} from "./runtime-common.mjs";
import { loadConfig } from "./config.mjs";
import { createClient } from "./http-client.mjs";
import { deriveOvSessionId, loadState, saveState } from "./state.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("bootstrap");

async function main() {
  // 1. Ensure MCP server runtime is installed
  const paths = getRuntimePaths();
  const expectedState = await computeSourceState(paths);
  try {
    await ensureRuntimeInstalled(paths, expectedState);
  } catch (err) {
    process.stderr.write(
      `[openviking-context-engine] Failed to prepare MCP runtime: ${err instanceof Error ? err.message : String(err)}\n`,
    );
  }

  // 2. Create/touch OV session
  try {
    const client = createClient(cfg);
    const healthy = await client.healthCheck();
    if (healthy) {
      // Read stdin for session_id if available
      let ccSessionId = "unknown";
      try {
        const chunks = [];
        process.stdin.resume();
        process.stdin.setTimeout?.(500);
        for await (const chunk of process.stdin) chunks.push(chunk);
        const input = JSON.parse(Buffer.concat(chunks).toString());
        ccSessionId = input.session_id || "unknown";
      } catch { /* no stdin or timeout — use default */ }

      const ovSessionId = deriveOvSessionId(ccSessionId);
      const result = await client.createSession(ovSessionId);
      log("session_created", { ccSessionId, ovSessionId, result });

      // Initialize state
      const state = await loadState(ccSessionId);
      state.ovSessionId = ovSessionId;
      state.createdAt = Date.now();
      await saveState(ccSessionId, state);
    } else {
      log("skip", { reason: "server_unhealthy" });
    }
  } catch (err) {
    logError("session_create", err);
  }
}

main().catch((err) => {
  logError("uncaught", err);
  process.exit(0);
});
