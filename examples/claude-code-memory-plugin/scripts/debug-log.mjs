/**
 * Shared structured debug logger for Claude Code hook scripts.
 *
 * Activation: OPENVIKING_DEBUG=1 env var  OR  claude_code.debug: true in ov.conf.
 * Log path:   OPENVIKING_DEBUG_LOG env var OR  ~/.openviking/logs/cc-hooks.log.
 * Format:     JSON Lines — { ts, hook, stage, data } | { ts, hook, stage, error }.
 *
 * When inactive, log() and logError() are zero-cost no-ops.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { loadConfig } from "./config.mjs";

let _cfg;
function cfg() {
  if (!_cfg) _cfg = loadConfig();
  return _cfg;
}

function ensureDir(filePath) {
  try {
    mkdirSync(dirname(filePath), { recursive: true });
  } catch { /* already exists or no permission — will fail on write */ }
}

function writeLine(filePath, obj) {
  try {
    appendFileSync(filePath, JSON.stringify(obj) + "\n");
  } catch { /* best effort — never crash the hook */ }
}

function localISO() {
  const d = new Date();
  const off = d.getTimezoneOffset();
  const sign = off <= 0 ? "+" : "-";
  const abs = Math.abs(off);
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().replace("Z",
    `${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`);
}

const noop = () => {};

/**
 * @param {string} hookName — e.g. "auto-recall" or "auto-capture"
 * @param {{ debug?: boolean, debugLogPath?: string }} [overrideCfg]
 *        Pass a config object directly (avoids re-loading ov.conf in test scripts).
 * @returns {{ log: (stage: string, data: any) => void, logError: (stage: string, err: any) => void }}
 */
export function createLogger(hookName, overrideCfg) {
  const c = overrideCfg || cfg();
  if (!c.debug) return { log: noop, logError: noop };

  const logPath = c.debugLogPath;
  ensureDir(logPath);

  function log(stage, data) {
    writeLine(logPath, { ts: localISO(), hook: hookName, stage, data });
  }

  function logError(stage, err) {
    const error = err instanceof Error
      ? { message: err.message, stack: err.stack }
      : String(err);
    writeLine(logPath, { ts: localISO(), hook: hookName, stage, error });
  }

  return { log, logError };
}
