/**
 * Configuration for the Kimi Code memory plugin.
 *
 * Every knob is declared once in the shared schema and assembled by
 * `buildPluginConfig("kimicode", …)`, so this file owns only what the host
 * knows: where the payload's workspace is, which manifest reports the version,
 * and the log file this plugin writes.
 *
 * The host re-resolves the workspace layer per event. A hook process starts in
 * the plugin root, which is not the user's project, so `config.mjs` takes the
 * payload's `cwd` and the stage passes it down. Re-resolving late is safe
 * because a workspace file may not carry connection or credential keys: the
 * base URL and API key cannot move under a logger or fetch helper already built
 * from the first load.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildPluginConfig } from "./shared/plugin-config.mjs";

export const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MANIFEST_PATH = join(PLUGIN_ROOT, "kimi.plugin.json");

/** The host harness ID this adapter registers itself under. */
export const HARNESS = "kimicode";

/** The OpenViking session prefix. Changing it orphans every existing session. */
export const SESSION_PREFIX = "kc-";

let manifestVersion = null;

/** The version in `kimi.plugin.json`, which is what the host installed. */
export function pluginVersion() {
  if (manifestVersion === null) {
    try {
      manifestVersion = String(JSON.parse(readFileSync(MANIFEST_PATH, "utf8")).version || "");
    } catch {
      manifestVersion = "";
    }
  }
  return manifestVersion;
}

/**
 * The full plugin configuration for one directory.
 *
 * `cwd` is the session's directory from the hook payload — never the plugin
 * root. An empty one falls back to the process directory, which is only right
 * for a caller that has no payload (doctor, recover, the MCP proxy).
 */
export function loadConfig(cwd = process.cwd(), { env = process.env } = {}) {
  return {
    ...buildPluginConfig(HARNESS, {
      cwd,
      env,
      version: pluginVersion() || env.OPENVIKING_INTEGRATION_VERSION,
      logFile: "kimicode-hooks.log",
    }),
    clientId: HARNESS,
  };
}

/**
 * The configuration for a session's own directory, or null when it cannot be
 * resolved.
 *
 * The sweep is started by whichever project opened first, and it must not use
 * that project's workspace file to configure a session from somewhere else. A
 * caller that has no recorded directory gets `null` and skips the session
 * rather than guessing.
 */
export function resolveConfigForCwd(cwd, { env = process.env, base = null } = {}) {
  const dir = String(cwd || "").trim();
  if (!dir) return null;
  try {
    const resolved = loadConfig(dir, { env });
    if (!base) return resolved;
    // The connection comes from the environment and the credential files, which
    // are the same for every directory. A caller that already resolved them —
    // a hook, or a test that injected a fake server — keeps that connection:
    // only the workspace layer may differ per directory.
    return {
      ...resolved,
      baseUrl: base.baseUrl || resolved.baseUrl,
      mcpUrl: base.mcpUrl || resolved.mcpUrl,
      apiKey: base.apiKey || resolved.apiKey,
      account: base.account || resolved.account,
      user: base.user || resolved.user,
      sendIdentityHeaders: base.sendIdentityHeaders,
      apiKeySource: base.apiKeySource,
      credentialSource: base.credentialSource,
    };
  } catch {
    return null;
  }
}

/** The manifest as the host parsed it, for doctor and the contract tests. */
export function readManifest(path = MANIFEST_PATH) {
  return JSON.parse(readFileSync(path, "utf8"));
}
