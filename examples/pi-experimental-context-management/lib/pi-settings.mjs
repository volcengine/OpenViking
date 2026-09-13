/**
 * Reading pi's own `settings.json`, without importing pi.
 *
 * The only value the context-window mode needs from it is
 * `compaction.reserveTokens`: pi auto-compacts as soon as
 * `contextTokens > contextWindow - reserveTokens`, so the hard reminder has to
 * be clamped under that line or the agent's notes are never written.
 *
 * pi merges the project file over the global one (`SettingsManager`), so the
 * project value wins here as well. Everything is best effort — a missing or
 * malformed file yields the pi default (16384).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** pi's own default when `compaction.reserveTokens` is unset. */
export const DEFAULT_RESERVE_TOKENS = 16384;

/**
 * `compaction.reserveTokens` of one parsed settings object, or null when the
 * object does not carry a usable value.
 */
export function reserveTokensFromSettings(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return null;
  const compaction = settings.compaction;
  if (!compaction || typeof compaction !== "object" || Array.isArray(compaction)) return null;
  const value = Number(compaction.reserveTokens);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.floor(value);
}

/**
 * First usable `compaction.reserveTokens` across the given file contents, in
 * order (project file first), else {@link DEFAULT_RESERVE_TOKENS}.
 *
 * `contents` holds raw file text or null for "file missing"; parse errors are
 * treated as "missing" so one broken file cannot hide the other one.
 */
export function pickReserveTokens(contents) {
  for (const raw of Array.isArray(contents) ? contents : []) {
    if (typeof raw !== "string" || !raw.trim()) continue;
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      continue;
    }
    const value = reserveTokensFromSettings(parsed);
    if (value !== null) return value;
  }
  return DEFAULT_RESERVE_TOKENS;
}

/**
 * The settings files pi reads, project first.
 *
 * `agentDir` mirrors pi's `PI_CODING_AGENT_DIR` override; without it the global
 * file sits at `~/<configDir>/agent/settings.json`, the same place `getAgentDir()`
 * points at.
 */
export function settingsPaths(opts = {}) {
  const cwd = String(opts.cwd || process.cwd());
  const configDirName = String(opts.configDirName || ".pi");
  const homeDir = String(opts.homeDir || process.env.HOME || "");
  const agentDir = String(opts.agentDir || "");
  const paths = [join(cwd, configDirName, "settings.json")];
  if (agentDir) paths.push(join(agentDir, "settings.json"));
  else if (homeDir) paths.push(join(homeDir, configDirName, "agent", "settings.json"));
  return paths;
}

/**
 * `compaction.reserveTokens` from `<cwd>/<configDir>/settings.json`, else
 * `~/<configDir>/agent/settings.json`, else {@link DEFAULT_RESERVE_TOKENS}.
 *
 * `readFile` is injected so the resolution order is testable without touching
 * the real filesystem.
 */
export function readReserveTokens(opts = {}) {
  const read =
    typeof opts.readFile === "function"
      ? opts.readFile
      : (path) => {
          try {
            return readFileSync(path, "utf8");
          } catch {
            return null;
          }
        };
  const contents = [];
  for (const path of settingsPaths(opts)) {
    let raw = null;
    try {
      raw = read(path);
    } catch {
      raw = null;
    }
    contents.push(typeof raw === "string" ? raw : null);
  }
  return pickReserveTokens(contents);
}
