/**
 * Configuration for the Claude Code OpenViking memory plugin.
 *
 * Every knob is declared once in `shared/config-schema.mjs` and the whole
 * configuration is assembled by `buildPluginConfig()`, which reads the layers
 * in this order:
 *
 *   env (OPENVIKING_*) → workspace `.openviking/config*.json` and the machine
 *   registry → ovcli.conf `plugin.claude_code` → ovcli.conf `plugin` →
 *   ov.conf's `claude_code` section (legacy) → the schema's defaults
 *
 * What stays here is what only this harness knows: whether the plugin is
 * enabled at all, the file `configPath` has always named, and the tri-state
 * digest mode.
 *
 * Enable/disable:
 *   - OPENVIKING_MEMORY_ENABLED env var (0/false/no = off, 1/true/yes = on)
 *   - claude_code.enabled field in ov.conf (false = off)
 *   - Fallback: enabled when ov.conf or ovcli.conf exists, disabled otherwise
 *
 * Connection and credentials are not knobs and never come from a workspace
 * file: OPENVIKING_URL / OPENVIKING_BASE_URL, OPENVIKING_API_KEY /
 * OPENVIKING_BEARER_TOKEN, OPENVIKING_ACCOUNT, OPENVIKING_USER, then
 * ovcli.conf, then ov.conf's server section.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

import { buildPluginConfig, normalizeRewriteMode } from "./shared/plugin-config.mjs";

const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const MANIFEST_URL = new URL("../.claude-plugin/plugin.json", import.meta.url);

function envBool(name) {
  const v = process.env[name];
  if (v == null || v === "") return undefined;
  const lower = v.trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no") return false;
  if (lower === "1" || lower === "true" || lower === "yes") return true;
  return undefined;
}

/**
 * Try to load and parse a JSON config file. Returns parsed object or null.
 */
function tryLoadJsonFile(envVar, defaultPath) {
  const configPath = resolvePath(
    (process.env[envVar] || defaultPath).replace(/^~/, homedir()),
  );

  let raw;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    return null;
  }

  try {
    return { configPath, file: JSON.parse(raw) };
  } catch {
    return null;
  }
}

/**
 * Determine whether the plugin is enabled.
 *
 * Priority:
 *   1. OPENVIKING_MEMORY_ENABLED env var
 *   2. claude_code.enabled in ov.conf
 *   3. Whether ov.conf or ovcli.conf exists and is parseable
 *
 * When force-enabled via env var (=1) without config files, the caller must
 * provide connection info via other env vars (OPENVIKING_URL, etc.).
 */
export function isPluginEnabled() {
  const envEnabled = envBool("OPENVIKING_MEMORY_ENABLED");
  if (envEnabled !== undefined) return envEnabled;

  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  if (ovConf) {
    const cc = ovConf.file.claude_code || {};
    if (cc.enabled === false) return false;
    return true;
  }

  // No ov.conf — check if ovcli.conf exists (sufficient for connection info)
  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);
  if (cliConf) return true;

  return false;
}

/**
 * Load the full plugin configuration.
 *
 * `cwd` selects the workspace layer (`.openviking/config.json` and the
 * registry entry for that directory). It defaults to this process's directory,
 * which is all a hook knows at module load; a hook whose payload names the
 * session's directory calls this again with it. Re-resolving that late is safe
 * because a workspace file may not carry connection or credential keys, so
 * baseUrl/apiKey cannot move — loggers and fetch helpers built from the first
 * load stay valid.
 */
export function loadConfig(cwd = process.cwd()) {
  const config = buildPluginConfig("claude-code", {
    cwd,
    manifestUrl: MANIFEST_URL,
    logFile: "cc-hooks.log",
    rootKeyFallback: true,
  });

  return {
    ...config,
    // `configPath` reports whichever file parsed, ov.conf first, and predates
    // the pair of paths beside it.
    configPath: config.ovPath || config.cliPath || null,
    credentialPath: config.credentialPath || null,

    // Digest compression defaults to auto: prefer the local host CLI and fall
    // back to the server when it is unavailable. A failed digest still falls
    // back to the uncompressed context block. `recallRewrite` keeps the
    // internal field name because the shared core maps this mode to the
    // server's `rewrite` request field; OPENVIKING_RECALL_REWRITE is the older
    // env spelling and still works.
    recallRewrite: normalizeRewriteMode(
      process.env.OPENVIKING_RECALL_COMPRESS
        ?? process.env.OPENVIKING_RECALL_REWRITE
        ?? config.recallCompress,
      "auto",
    ),
  };
}
