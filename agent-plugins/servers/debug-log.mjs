/**
 * Structured debug logger for the OpenViking Agent Plugins 1.0 package.
 *
 * VENDORED (adapted) from examples/claude-code-memory-plugin/scripts/debug-log.mjs —
 * keep in sync with that file.
 *
 * Activation: OPENVIKING_DEBUG=1 env var.
 * Log path:   OPENVIKING_DEBUG_LOG env var OR ~/.openviking/logs/agent-plugins.log.
 * Format:     JSON Lines — { ts, hook, stage, data } | { ts, hook, stage, error }.
 *
 * When inactive, log() and logError() are zero-cost no-ops.
 */

import { loadConfig } from "./config.mjs";
import { createLogger as createSharedLogger } from "./shared/debug-log.mjs";

let _cfg;
function cfg() {
  if (!_cfg) _cfg = loadConfig();
  return _cfg;
}

/**
 * @param {string} hookName — e.g. "mcp-proxy"
 * @param {{ debug?: boolean, debugLogPath?: string }} [overrideCfg]
 *        Pass a config object directly (avoids re-loading config in tests).
 * @returns {{ log: (stage: string, data: any) => void, logError: (stage: string, err: any) => void }}
 */
export function createLogger(hookName, overrideCfg) {
  return createSharedLogger(hookName, overrideCfg || cfg());
}
