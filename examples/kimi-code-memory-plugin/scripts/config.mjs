/**
 * Configuration for the Kimi Code OpenViking memory plugin.
 *
 * Every knob is declared once in `shared/config-schema.mjs` and assembled by
 * `buildPluginConfig()`, reading the layers in this order:
 *
 *   env (OPENVIKING_*) → workspace `.openviking/config*.json` and the machine
 *   registry → ovcli.conf `plugin.kimicode` → ovcli.conf `plugin` → ov.conf's
 *   `kimicode` section (legacy) → the schema's defaults
 *
 * `kimicode` is already a registered harness key (shared/config-schema.mjs
 * HARNESS_KEYS), so `ov config`-managed overrides resolve without new code.
 *
 * Credentials come from `shared/credentials.mjs` through buildPluginConfig:
 * env vars win when set (auto), otherwise the active ovcli.conf; a local
 * 127.0.0.1:1933 server needs neither.
 *
 * The stdio MCP proxy builds its connection from this same loadConfig(), so
 * hooks and MCP calls cannot drift apart on identity.
 *
 * SCAFFOLD: mirrors examples/codex-memory-plugin/scripts/config.mjs.
 * Implementation: DESIGN.md §4 / handoff doc §10.
 */

import { buildPluginConfig } from "./shared/plugin-config.mjs";

const MANIFEST_URL = new URL("../kimi.plugin.json", import.meta.url);

/**
 * Wire protocol version this plugin's decoder was verified against
 * (DESIGN.md §5.7). A session whose metadata declares a NEWER version is
 * skipped with a warning rather than mis-parsed. Declared here, not in the
 * shared schema, because it is a property of this decoder — bumping it is a
 * code change with a test, not a user knob.
 */
export const WIRE_PROTOCOL_VERSION = "1.5";

export function loadConfig(cwd = process.cwd(), { env = process.env } = {}) {
  const config = buildPluginConfig("kimicode", {
    cwd,
    env,
    manifestUrl: MANIFEST_URL,
    logFile: "kimicode-hooks.log",
  });

  return {
    ...config,
    wireProtocolVersion: WIRE_PROTOCOL_VERSION,
  };
}
