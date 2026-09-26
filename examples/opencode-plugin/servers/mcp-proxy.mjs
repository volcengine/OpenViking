#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking OpenCode plugin.
 *
 * OpenCode starts this process as a local MCP server. The proxy resolves its
 * connection through the plugin's own `loadConfig()`, forwards JSON-RPC
 * requests to the server's /mcp endpoint, and keeps stdout protocol-clean.
 */

import { resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"
import { loadConfig } from "../lib/config.mjs"
import { createLogger } from "../lib/shared/debug-log.mjs"
import { toMcpProxyConfig } from "../lib/shared/mcp-proxy-config.mjs"
import { createOpenVikingMcpProxy } from "../lib/shared/mcp-proxy-core.mjs"

const PLUGIN_ROOT = resolvePath(fileURLToPath(import.meta.url), "..", "..")

export function readProxyConfig(env = process.env) {
  return toMcpProxyConfig(loadConfig(PLUGIN_ROOT, undefined, { env }), { env })
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start()
}
