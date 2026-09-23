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
 * What stays here is what only this harness knows: what counts as having configured a compressor.
 *
 * Credential source:
 *   - Default (auto): env-var credentials win when any credential env var is
 *     set; otherwise the active ovcli.conf is used, so `ov config switch`
 *     changes hooks, MCP, and in-process `ov` commands together on next launch.
 *   - Set OPENVIKING_CREDENTIAL_SOURCE=cli to force ovcli.conf, or =env to
 *     read env vars only, with neither config file.
 *   - Without env vars or ovcli.conf, ov.conf/defaults are used.
 *
 * The stdio MCP proxy builds its connection from this same `loadConfig()`, so
 * the auto-capture/auto-recall hooks and MCP calls cannot drift apart on
 * identity.
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

/**
 * `cwd` selects the workspace layer (`.openviking/config.json` and the registry
 * entry for that directory). It defaults to this process's directory, which is
 * all a hook knows at module load; a hook whose payload names the session's
 * directory calls this again with it. Re-resolving that late is safe because a
 * workspace file may not carry connection or credential keys, so baseUrl/apiKey
 * cannot move — loggers and fetch helpers built from the first load stay valid.
 */
export function loadConfig(cwd = process.cwd(), { env = process.env } = {}) {
  const config = buildPluginConfig("codex", {
    cwd,
    env,
    manifestUrl: MANIFEST_URL,
    logFile: "codex-hooks.log",
  });

  return {
    ...config,
    // Not `configured.has`: what makes a compressor configured here is having
    // been told which model to run, not having named the switch.
    recallCompressConfigured: Boolean(config.recallCompressModel || config.recallCompressThinking),
  };
}
