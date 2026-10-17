#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking CodeBuddy plugin.
 *
 * CodeBuddy starts this process as a local stdio MCP server (declared in the
 * plugin's `.mcp.json`). The proxy resolves its connection through the hooks'
 * own `loadConfig()`, so hooks and MCP can never disagree about the server,
 * account or user, and it keeps stdout protocol-clean.
 *
 * Note for the MCP endpoint: `enable_dns_rebinding_protection=false` on the
 * OpenViking side, so dialing a bare wg address works (docs/HOST-CONTRACT.md §2).
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
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
