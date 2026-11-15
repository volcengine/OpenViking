// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Shared structured debug logger for OpenViking memory plugin hook scripts.
 *
 * Harness-specific wrappers load config and pass {debug, debugLogPath}. This
 * module stays path-agnostic so it can be vendored into each plugin snapshot.
 */

import { appendFileSync, mkdirSync, renameSync, statSync } from "node:fs";
import { dirname } from "node:path";

// The log holds session ids, `viking://` URIs and error text. It is the
// operator's own diagnostic file, so it gets the same private permissions the
// pending queue uses rather than whatever the umask happens to allow.
const LOG_DIR_MODE = 0o700;
const LOG_FILE_MODE = 0o600;

// A hook that logs on every event must not fill the disk. Past this size the
// file is rotated once, so `doctor` can still show the most recent run.
const MAX_LOG_BYTES = 8 * 1024 * 1024;

function ensureDir(filePath) {
  try {
    mkdirSync(dirname(filePath), { recursive: true, mode: LOG_DIR_MODE });
  } catch { /* best effort */ }
}

function writeLine(filePath, obj) {
  try {
    const info = statSync(filePath, { throwIfNoEntry: false });
    if (info && info.size > MAX_LOG_BYTES) {
      // One generation of history is enough for `doctor`, and moving the file
      // aside is cheaper than rewriting it in place.
      renameSync(filePath, `${filePath}.1`);
    }
    appendFileSync(filePath, `${JSON.stringify(obj)}\n`, { mode: LOG_FILE_MODE });
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
