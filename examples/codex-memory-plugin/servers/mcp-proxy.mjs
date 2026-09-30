#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking Codex plugin.
 *
 * Codex starts this process as a local stdio MCP server. The proxy resolves its
 * connection through the hooks' own `loadConfig()`, forwards JSON-RPC requests
 * to the server's /mcp endpoint, and keeps stdout protocol-clean.
 */

import { realpathSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../scripts/config.mjs";
import { createLogger } from "../scripts/debug-log.mjs";
import { toMcpProxyConfig } from "../scripts/shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";

export function readProxyConfig(env = process.env) {
  return toMcpProxyConfig(loadConfig(undefined, { env }), { env });
}

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectRun()) {
  createOpenVikingMcpProxy({
    readConfig: readProxyConfig,
    loggerFactory: createLogger,
  }).start();
}
