/**
 * Shared structured debug logger for Codex hook scripts.
 *
 * Activation: OPENVIKING_DEBUG=1 env var OR codex.debug=true in ovcli.conf/ov.conf.
 * Log path:   OPENVIKING_DEBUG_LOG env var OR ~/.openviking/logs/codex-hooks.log.
 * Format:     JSON Lines — { ts, hook, stage, data } | { ts, hook, stage, error }.
 */

import { loadConfig } from "./config.mjs";
import { createLogger as createSharedLogger } from "./shared/debug-log.mjs";

let _cfg;
function cfg() {
  if (!_cfg) _cfg = loadConfig();
  return _cfg;
}

export function createLogger(hookName, overrideCfg) {
  return createSharedLogger(hookName, overrideCfg || cfg());
}
