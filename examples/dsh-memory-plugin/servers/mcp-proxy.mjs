#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking DSH bundle.
 *
 * DSH's MCP bridge starts this process as a local stdio MCP server. The proxy
 * resolves its connection through the same `resolveConfig()` as the in-process
 * runtime, from the child environment the bundle builds in `mcp.mjs`: DSH
 * scrubs credential-shaped names out of what it inherits, and values that came
 * from the Cordis patch are invisible to a subprocess otherwise.
 */

import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveConfig } from "../config.mjs";
import { createLogger } from "../shared/debug-log.mjs";
import { toMcpProxyConfig } from "../shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../shared/mcp-proxy-core.mjs";

export function readProxyConfig(env = process.env, cwd = process.cwd()) {
  const cfg = resolveConfig({}, env, cwd);
  return toMcpProxyConfig(cfg, {
    env,
    // Not gated through `resolveMcpActorPeerId` like the other proxies: DSH's
    // parent process resolves the peer per session and hands it over in the
    // child env. Empty means it has none, and deriving one from wherever DSH
    // launched this process would send a peer the runtime does not.
    peerId: String(env.OPENVIKING_PEER_ID || "").trim(),
    debug: Boolean(env.OV_DEBUG_LOG),
    debugLogPath: env.OV_DEBUG_LOG || "",
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
