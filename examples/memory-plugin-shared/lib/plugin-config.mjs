/**
 * Per-harness plugin settings from ovcli.conf.
 *
 * ovcli.conf carried connection fields only, so every harness had to keep its
 * tuning knobs in ov.conf's harness section — a server-side file that a
 * client-side plugin has no business editing. The `plugin` section fixes that:
 * shared keys apply to every harness, and a per-harness object overrides them.
 *
 *   {
 *     "url": "...", "api_key": "...",
 *     "plugin": {
 *       "recallQueryExpansion": "off",
 *       "recallCompress": "auto"
 *     }
 *   }
 *
 * Resolution stays env → ovcli.conf plugin.<harness> → ovcli.conf plugin →
 * ov.conf harness section (legacy) → defaults.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");

export const HARNESS_KEYS = {
  claudeCode: "claude_code",
  codex: "codex",
  opencode: "opencode",
  cursor: "cursor",
  trae: "trae",
  pi: "pi",
};

function tryLoadJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

/**
 * Merge the shared plugin section with the harness-specific override.
 * Returns a flat settings object; unknown keys pass through untouched so a
 * harness can add its own knobs without touching this module.
 */
export function loadPluginSettings(harness, env = process.env) {
  const path = resolvePath(
    (env.OPENVIKING_CLI_CONFIG_FILE || DEFAULT_OVCLI_CONF_PATH).replace(/^~/, homedir()),
  );
  const file = tryLoadJson(path);
  const plugin = file && typeof file.plugin === "object" && file.plugin ? file.plugin : {};

  const shared = {};
  for (const [key, value] of Object.entries(plugin)) {
    if (value && typeof value === "object" && !Array.isArray(value)) continue;
    shared[key] = value;
  }
  const scoped = harness && plugin[harness] && typeof plugin[harness] === "object"
    ? plugin[harness]
    : {};

  return { ...shared, ...scoped };
}

const REWRITE_MODES = new Set(["off", "client", "server", "auto"]);

/**
 * Normalize the tri-state rewrite knob.
 *
 * off    — never compress
 * client — always compress locally through the host CLI
 * server — always ask the server for a digest
 * auto   — prefer local (cost lands on the user's own subscription), fall back
 *          to the server when no healthy host CLI is available
 */
export function normalizeRewriteMode(value, fallback = "off") {
  const raw = String(value ?? "").trim().toLowerCase();
  if (REWRITE_MODES.has(raw)) return raw;
  if (raw === "1" || raw === "true" || raw === "yes") return "auto";
  if (raw === "0" || raw === "false" || raw === "no") return "off";
  return fallback;
}
