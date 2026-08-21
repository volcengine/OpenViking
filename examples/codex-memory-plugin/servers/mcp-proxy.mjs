#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking Codex plugin.
 *
 * Codex starts this process as a local stdio MCP server. The proxy reads the
 * same OpenViking credential sources as the lifecycle hooks, forwards JSON-RPC
 * requests to the server's /mcp endpoint, and keeps stdout protocol-clean.
 */

import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../scripts/config.mjs";
import { createLogger } from "../scripts/debug-log.mjs";
import { resolveOpenVikingCredentials } from "../scripts/ov-credentials.mjs";
import { buildMcpProxyConfig } from "../scripts/shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";
import { resolveEffectivePeerId } from "../scripts/shared/workspace-peer.mjs";

export { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";

function readProxyConfig() {
  const creds = resolveOpenVikingCredentials();
  const cfg = loadConfig();
  return buildMcpProxyConfig({
    baseUrl: creds.baseUrl,
    mcpUrl: creds.mcpUrl,
    apiKey: creds.apiKey,
    account: creds.account,
    user: creds.user,
    peerId: resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId,
    userAgent: cfg.userAgent,
    timeoutMs: cfg.timeoutMs,
    debug: cfg.debug,
    debugLogPath: cfg.debugLogPath,
    credentialSource: creds.credentialSource,
    credentialPath: creds.cliPath || creds.ovPath,
    watchedPaths: [creds.cliPath, creds.ovPath, creds.cliPathCandidate],
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({
    readConfig: readProxyConfig,
    loggerFactory: createLogger,
  }).start();
}
