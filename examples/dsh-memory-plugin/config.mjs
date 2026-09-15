import { buildPluginConfig } from "./shared/plugin-config.mjs";
import { loadCredentialFiles } from "./shared/credentials.mjs";

export const PLUGIN_VERSION = "0.4.0";

/**
 * Namespace for the bridged OpenViking MCP tools. DSH publishes every MCP tool
 * as `mcp__<serverName>__<rawName>`, so this string is part of the
 * model-facing contract: changing it renames all of them.
 */
export const MCP_SERVER_NAME = "openviking";

/**
 * Resolve the plugin's configuration.
 *
 * `input` is what the cordis host hands the plugin — this harness has no config
 * file of its own. Every knob is declared in `shared/config-schema.mjs`, and
 * `ovcli.conf`'s `plugin` section outranks the host's input so one file
 * configures every harness and `ov config switch` moves them together.
 * Connection fields are the exception: an endpoint or key named by the host is
 * the more specific answer and stays ahead of the credential chain.
 */
export function resolveConfig(input = {}, env = process.env, cwd = process.cwd()) {
  // ov.conf's `dsh` section is the legacy layer here as everywhere else, but
  // the cordis patch shares that slot; the host named this process's settings,
  // so it wins the overlap.
  const ovSection = loadCredentialFiles(env).ovFile.dsh;
  const config = buildPluginConfig("dsh", {
    env,
    cwd,
    legacy: { ...(ovSection && typeof ovSection === "object" ? ovSection : {}), ...input },
    version: PLUGIN_VERSION,
    hostInput: {
      peerId: input.peerId,
      account: input.account,
      user: input.user,
      apiKey: input.apiKey,
      baseUrl: input.endpoint,
    },
    deriveEffectivePeer: true,
  });

  return { ...config, peerId: config.effectivePeer.peerId };
}
