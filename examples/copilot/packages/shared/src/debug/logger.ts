/**
 * Append-only JSONL logger for the Copilot plugins.
 *
 * - No-op when `enabled` is false (so it's safe to wire into hot paths
 *   regardless of config).
 * - Writes one JSON object per line to `path`. Shape:
 *     {"ts":"2026-05-07T20:30:00.000Z","scope":"recall","event":"hit","query":"…"}
 * - Scans fields for secret-like keys and redacts the value before writing.
 *   Never writes apiKey, bearer tokens, etc. to disk.
 * - Rotates when the file crosses `maxBytes`: current is renamed to `<path>.1`
 *   (overwriting any prior `.1`), and a fresh file is started.
 *
 * Path resolution lives in config.ts (env > host override > default), so this
 * module just consumes `cfg.debugLogPath` as the absolute target.
 */

import {
  appendFileSync,
  mkdirSync,
  renameSync,
  statSync,
} from "node:fs";
import { dirname } from "node:path";
import type { PluginConfig } from "../config.js";

/** Default rotation cap: 10 MB. */
export const DEFAULT_MAX_BYTES = 10 * 1024 * 1024;

/**
 * Keys whose values should never appear in the log file. Matched
 * case-insensitively against the literal field name (no path traversal).
 */
const SECRET_KEY_RE = /^(api[_-]?key|bearer|token|secret|password|authorization)$/i;
const REDACTED = "[REDACTED]" as const;

export interface DebugLogger {
  /** Append a structured log entry. No-op when disabled. */
  log(event: string, fields?: Record<string, unknown>): void;
  /** Return a logger that adopts a different scope (e.g. "recall", "capture"). */
  child(scope: string): DebugLogger;
}

export interface CreateDebugLoggerOptions {
  /** Override the rotation size cap (bytes). Default 10 MB. */
  maxBytes?: number;
  /** Override the scope label embedded in each line. Default "shared". */
  scope?: string;
  /** Inject a clock for tests. Default `() => new Date().toISOString()`. */
  now?: () => string;
}

/**
 * Build a logger from a PluginConfig. Reads `cfg.debug` and `cfg.debugLogPath`.
 */
export function createDebugLogger(
  cfg: Pick<PluginConfig, "debug" | "debugLogPath">,
  opts: CreateDebugLoggerOptions = {},
): DebugLogger {
  const enabled = cfg.debug === true;
  const path = cfg.debugLogPath;
  const maxBytes = Math.max(1024, Math.floor(opts.maxBytes ?? DEFAULT_MAX_BYTES));
  const now = opts.now ?? (() => new Date().toISOString());
  const scope = opts.scope ?? "shared";

  return makeLogger(enabled, path, maxBytes, now, scope);
}

function makeLogger(
  enabled: boolean,
  path: string,
  maxBytes: number,
  now: () => string,
  scope: string,
): DebugLogger {
  let dirEnsured = false;

  function ensureDir(): void {
    if (dirEnsured) return;
    try {
      mkdirSync(dirname(path), { recursive: true });
      dirEnsured = true;
    } catch {
      // Best-effort: fall through; appendFile will surface the error.
    }
  }

  function rotateIfNeeded(nextLineBytes: number): void {
    let size = 0;
    try {
      size = statSync(path).size;
    } catch {
      return; // file doesn't exist yet
    }
    if (size + nextLineBytes <= maxBytes) return;
    try {
      renameSync(path, `${path}.1`); // overwrites any prior .1
    } catch {
      // If rename fails, stop trying to rotate this turn — better to keep
      // appending than to throw out of a debug-log call.
    }
  }

  function log(event: string, fields?: Record<string, unknown>): void {
    if (!enabled) return;
    ensureDir();
    const entry: Record<string, unknown> = {
      ts: now(),
      scope,
      event,
    };
    if (fields) {
      for (const [k, v] of Object.entries(fields)) {
        entry[k] = redactValue(k, v);
      }
    }
    let line: string;
    try {
      line = `${JSON.stringify(entry)}\n`;
    } catch {
      // Fall back to a flat representation when a value can't be serialised
      // (circular refs, BigInt, etc.). The user wanted *some* signal, not a
      // crashed hot path.
      line = `${JSON.stringify({ ts: entry["ts"], scope, event, error: "unserialisable_fields" })}\n`;
    }
    rotateIfNeeded(Buffer.byteLength(line, "utf8"));
    try {
      appendFileSync(path, line, { encoding: "utf8" });
    } catch {
      // Logger must never crash the caller. Silently drop.
    }
  }

  function child(nextScope: string): DebugLogger {
    return makeLogger(enabled, path, maxBytes, now, nextScope);
  }

  return { log, child };
}

/**
 * Redact a value when its key looks secret-like, otherwise recurse into
 * objects/arrays so a token nested under e.g. `headers.authorization` is
 * still scrubbed. Plain primitives are returned as-is. Cycles are replaced
 * with `"[CIRCULAR]"` so a self-referential object can't blow the stack.
 */
function redactValue(key: string, value: unknown, seen?: WeakSet<object>): unknown {
  if (SECRET_KEY_RE.test(key)) return REDACTED;
  if (value === null || value === undefined) return value;
  if (typeof value !== "object") return value;

  const visited = seen ?? new WeakSet<object>();
  if (visited.has(value as object)) return "[CIRCULAR]";
  visited.add(value as object);

  if (Array.isArray(value)) {
    return value.map((item, i) => redactValue(`${key}[${i}]`, item, visited));
  }
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = redactValue(k, v, visited);
  }
  return out;
}

/** Exposed for unit tests. */
export const __test__ = { redactValue };
