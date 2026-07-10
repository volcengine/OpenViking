/**
 * Shared structured debug logger for Claude Code hook scripts.
 *
 * Activation: OPENVIKING_DEBUG=1 env var  OR  claude_code.debug: true in ov.conf.
 * Log path:   OPENVIKING_DEBUG_LOG env var OR  ~/.openviking/logs/cc-hooks.log.
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
 * @param {string} hookName — e.g. "auto-recall" or "auto-capture"
 * @param {{ debug?: boolean, debugLogPath?: string }} [overrideCfg]
 *        Pass a config object directly (avoids re-loading ov.conf in test scripts).
 * @returns {{ log: (stage: string, data: any) => void, logError: (stage: string, err: any) => void }}
 */
export function createLogger(hookName, overrideCfg) {
  return createSharedLogger(hookName, overrideCfg || cfg());
}
