/**
 * Configuration for the Codex OpenViking memory plugin.
 *
 * Every knob is declared once in `shared/config-schema.mjs` and the whole
 * configuration is assembled by `buildPluginConfig()`, which reads the layers
 * in this order:
 *
 *   env (OPENVIKING_*) → workspace `.openviking/config*.json` and the machine
 *   registry → ovcli.conf `plugin.codex` → ovcli.conf `plugin` → ov.conf's
 *   `codex` section (legacy) → the schema's defaults
 *
 * What stays here is what only this harness knows: how it reads the digest
 * switch, and what counts as having configured a compressor.
 *
 * Credential source:
 *   - Default (auto): env-var credentials win when any credential env var is
 *     set; otherwise the active ovcli.conf is used, so `ov config switch`
 *     changes hooks, MCP, and in-process `ov` commands together on next launch.
 *   - Set OPENVIKING_CREDENTIAL_SOURCE=cli to force ovcli.conf, or =env to
 *     force env-var credentials.
 *   - Without env vars or ovcli.conf, ov.conf/defaults are used.
 *
 * The stdio MCP proxy calls the same resolver directly. Aligning the resolver
 * prevents identity drift between auto-capture/auto-recall hooks, MCP calls,
 * and child `ov` commands launched from inside Codex.
 *
 * File-path env vars:
 *   OPENVIKING_CLI_CONFIG_FILE  alternate ovcli.conf path  (preferred)
 *   OPENVIKING_CONFIG_FILE      alternate ov.conf path
 *
 * For backward compat, if only OPENVIKING_CONFIG_FILE is set and the file
 * it points at parses as an ovcli.conf (top-level `url`/`api_key`, no
 * `server` section), it is treated as ovcli.conf — earlier versions of
 * this plugin used OPENVIKING_CONFIG_FILE to mean either file.
 *
 * Connection / identity env vars:
 *   OPENVIKING_URL / OPENVIKING_BASE_URL
 *   OPENVIKING_API_KEY / OPENVIKING_BEARER_TOKEN
 *   OPENVIKING_AUTH_MODE
 *   OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_PEER_ID
 */

import { buildPluginConfig } from "./shared/plugin-config.mjs";

const MANIFEST_URL = new URL("../.codex-plugin/plugin.json", import.meta.url);

function configBool(value, fallback) {
  if (typeof value === "boolean") return value;
  const lower = String(value ?? "").trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no" || lower === "off") return false;
  if (lower === "1" || lower === "true" || lower === "yes" || lower === "on"
      || lower === "auto" || lower === "client") return true;
  return fallback;
}

/**
 * `cwd` selects the workspace layer (`.openviking/config.json` and the registry
 * entry for that directory). It defaults to this process's directory, which is
 * all a hook knows at module load; a hook whose payload names the session's
 * directory calls this again with it. Re-resolving that late is safe because a
 * workspace file may not carry connection or credential keys, so baseUrl/apiKey
 * cannot move — loggers and fetch helpers built from the first load stay valid.
 */
export function loadConfig(cwd = process.cwd()) {
  const config = buildPluginConfig("codex", {
    cwd,
    manifestUrl: MANIFEST_URL,
    logFile: "codex-hooks.log",
  });

  return {
    ...config,
    // Codex reads the compression knob as on/off; "auto" and "client" are the
    // Claude Code spellings of on, and mean the same thing here.
    recallCompress: configBool(config.recallCompress, true),
    // Not `configured.has`: what makes a compressor configured here is having
    // been told which model to run, not having named the switch.
    recallCompressConfigured: Boolean(config.recallCompressModel || config.recallCompressThinking),
  };
}
