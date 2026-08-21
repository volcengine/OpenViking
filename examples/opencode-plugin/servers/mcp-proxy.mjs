#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking OpenCode plugin.
 *
 * OpenCode starts this process as a local MCP server. The proxy reads the same
 * OpenViking credential sources as the lifecycle hooks, forwards JSON-RPC
 * requests to the server's /mcp endpoint, and keeps stdout protocol-clean.
 */

import { homedir } from "node:os"
import { join, resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"
import { loadConfig } from "../lib/config.mjs"
import { createLogger } from "../lib/shared/debug-log.mjs"
import { buildMcpProxyConfig } from "../lib/shared/mcp-proxy-config.mjs"
import { createOpenVikingMcpProxy } from "../lib/shared/mcp-proxy-core.mjs"
import { resolveEffectivePeerId } from "../lib/shared/workspace-peer.mjs"

export { createOpenVikingMcpProxy } from "../lib/shared/mcp-proxy-core.mjs"

function readProxyConfig() {
  const cfg = loadConfig(resolvePath(fileURLToPath(import.meta.url), "..", ".."))
  return buildMcpProxyConfig({
    baseUrl: cfg.endpoint,
    mcpUrl: cfg.mcpUrl,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    peerId: resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId,
    userAgent: cfg.userAgent,
    timeoutMs: cfg.timeoutMs,
    debug: cfg.debug,
    debugLogPath: cfg.debugLogPath,
    credentialSource: cfg.credentialSource,
    credentialPath: cfg.credentialPath || cfg.configPath,
    watchedPaths: [
      cfg.credentialPath,
      cfg.configPath,
      join(homedir(), ".config", "opencode", "openviking-config.json"),
    ],
  })
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start()
}
