/**
 * Shared structured debug logger for OpenViking memory plugin hook scripts.
 *
 * Harness-specific wrappers load config and pass {debug, debugLogPath}. This
 * module stays path-agnostic so it can be vendored into each plugin snapshot.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

function ensureDir(filePath) {
  try {
    mkdirSync(dirname(filePath), { recursive: true });
  } catch { /* best effort */ }
}

function writeLine(filePath, obj) {
  try {
    appendFileSync(filePath, JSON.stringify(obj) + "\n");
  } catch { /* best effort */ }
}

function localISO() {
  const d = new Date();
  const off = d.getTimezoneOffset();
  const sign = off <= 0 ? "+" : "-";
  const abs = Math.abs(off);
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().replace(
    "Z",
    `${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`,
  );
}

const noop = () => {};

export function createLogger(hookName, overrideCfg) {
  const c = overrideCfg || {};
  if (!c.debug) return { log: noop, logError: noop };

  const logPath = c.debugLogPath;
  if (!logPath) return { log: noop, logError: noop };
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
