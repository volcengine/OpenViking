#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking OpenCode plugin.
 *
 * OpenCode starts this process as a local MCP server. The proxy resolves its
 * connection through the plugin's own `loadConfig()`, forwards JSON-RPC
 * requests to the server's /mcp endpoint, and keeps stdout protocol-clean.
 */

import { realpathSync } from "node:fs"
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

function isDirectRun() {
  if (!process.argv[1]) return false
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url)
  }
}

if (isDirectRun()) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start()
}
