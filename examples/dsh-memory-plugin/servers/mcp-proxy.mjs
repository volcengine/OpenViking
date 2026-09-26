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
    // The child env carries the runtime's resolved peer, but the shared mapper
    // still decides whether the proxy may send it. In broad recall mode the
    // actor header must stay unset; otherwise a process launched in workspace A
    // narrows MCP searches for a session whose runtime is serving workspace B.
    debug: Boolean(env.OV_DEBUG_LOG),
    debugLogPath: env.OV_DEBUG_LOG || "",
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
